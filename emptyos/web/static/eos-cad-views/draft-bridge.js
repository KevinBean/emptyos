// draft-bridge.js - coordinates the parity Draft editor across layout panes.
//
// The mature draft editor still lives in /cad/pages/draft-workspace.js. During
// the layout-host migration, the five draft views register their panes here; once
// all panes exist, the bridge mounts the editor slices into those panes while the
// shared DraftStore remains the state spine.

import { mountLayout, unmountLayout } from '/cad/pages/draft-workspace.js';

const REQUIRED = ['draft-docbar', 'draft-tools', 'draft-canvas', 'draft-inspector', 'draft-cmdbar'];
let session = null;

function ensureSession(vctx) {
  if (!session || session.store !== vctx.store) {
    if (session && session.started) {
      try { unmountLayout(); } catch (e) { /* best-effort */ }
    }
    session = { store: vctx.store, vctx, panes: {}, mounted: new Set(), started: false };
  }
  return session;
}

async function maybeStart(s) {
  if (s.started) return;
  if (!REQUIRED.every((k) => s.panes[k])) return;
  s.started = true;
  await mountLayout({
    root: null,
    workspace: { id: 'draft', label: '2D Drawing', api_prefix: '/cad/api' },
    params: s.vctx.params || new URLSearchParams(location.search),
    api: s.vctx.api,
    store: s.store,
    setStatus: s.vctx.setStatus,
    setTitle: s.vctx.setTitle,
    navigate: (wid, q) => { location.href = '/cad/?workspace=' + encodeURIComponent(wid) + (q ? '&' + q : ''); },
  }, s.panes, s.store);
}

export async function mountDraftPane(vctx, area) {
  const s = ensureSession(vctx);
  s.vctx = vctx;
  s.panes[area] = vctx.pane;
  s.mounted.add(area);
  await maybeStart(s);
}

export function teardownDraftPane(area) {
  if (!session) return;
  session.mounted.delete(area);
  delete session.panes[area];
  if (session.mounted.size) return;
  if (session.started) {
    try { unmountLayout(); } catch (e) { /* best-effort */ }
  }
  session = null;
}
