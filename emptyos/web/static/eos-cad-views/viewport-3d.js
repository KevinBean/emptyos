// eos-cad-views/viewport-3d.js — the 3D viewport view (the one view that holds an
// engine). It does NOT construct a viewport per mount: the FIRST time it mounts it
// lazily creates the single shared CadViewport on its pane and stores it on the
// CadStore; every later mount just reparents that one viewport (attachTo). On
// teardown it parks the canvas (parkCanvas) — never destroys it — so a layout
// switch reflows panes with zero GL teardown and no state loss. See
// .claude/rules/cad-layouts.md.

import { defineView } from '/static/eos-cad-view.js';
import { CadViewport } from '/static/eos-cad-viewport.js';

function selectedFeatureId(store) {
  const selected = store.selection;
  if (selected == null) return null;
  const object = store.objectByOid ? store.objectByOid(selected) : null;
  if (!object) return selected;
  const owned = new Set(object.feature_ids || []);
  const features = (store.doc && store.doc.features) || [];
  const result = features.find((feature) => owned.has(feature.id) && feature.op === 'subtract');
  return result ? result.id : ((object.feature_ids || []).at(-1) || null);
}

const STYLES = `
  .cadv-3d-host { position: absolute; inset: 0; overflow: hidden; }
  .cadv-3d-viewbar {
    position: absolute; top: 8px; right: 8px; z-index: 8;
    display: flex; gap: 4px; align-items: center; flex-wrap: wrap; justify-content: flex-end;
    max-width: min(460px, calc(100% - 16px)); pointer-events: none;
  }
  .cadv-3d-viewbar .eos-tool-btn {
    min-width: 44px; min-height: 40px; padding: 8px 12px;
    background: color-mix(in srgb, var(--bg-card) 92%, transparent);
    pointer-events: auto;
  }
  .cadv-3d-viewbar .eos-tool-segmented {
    pointer-events: auto;
    background: color-mix(in srgb, var(--bg-card) 92%, transparent);
  }
  .cadv-3d-viewbar .eos-tool-segment { pointer-events: auto; min-height: 40px; padding: 8px 12px; }
  .cadv-3d-scale {
    position: absolute; left: 8px; bottom: 8px; z-index: 8;
    max-width: min(520px, calc(100% - 16px)); padding: 4px 8px;
    border: 1px solid var(--border); border-radius: 8px;
    background: color-mix(in srgb, var(--bg-card) 88%, transparent);
    color: var(--text-secondary, var(--muted)); font-size: 11px;
    line-height: 1.4; pointer-events: none; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .cadv-3d-coords {
    position: absolute; right: 8px; bottom: 8px; z-index: 8;
    padding: 4px 8px; border: 1px solid var(--border); border-radius: 8px;
    background: color-mix(in srgb, var(--bg-card) 88%, transparent);
    color: var(--text-secondary, var(--muted)); font-size: 11px;
    font-variant-numeric: tabular-nums; line-height: 1.4;
    pointer-events: none; white-space: nowrap;
  }
  .cadv-3d-coords[hidden] { display: none; }
  @media (max-width: 640px) {
    .cadv-3d-viewbar { left: 8px; right: 8px; justify-content: flex-end; }
    .cadv-3d-viewbar .eos-tool-btn,
    .cadv-3d-viewbar .eos-tool-segment { min-height: 44px; padding: 8px 10px; }
    .cadv-3d-scale { bottom: 8px; font-size: 11px; max-width: min(60%, calc(100% - 16px)); }
    .cadv-3d-coords { bottom: 8px; }
  }
`;

// ── Live hover coordinate + measure tool (view-side state) ──
let _measurePts = [];   // world points captured while measure mode is active

function fmtCoords(vp, p) {
  const u = vp.docUnits();
  return 'x ' + vp.fmtValue(p.x) + '   y ' + vp.fmtValue(p.y) + '   z ' + vp.fmtValue(p.z) +
         (u ? '  ' + u : '');
}

// Bind the engine's per-pointermove callback to THIS pane's coord pill. Reassigned
// on every wireViewbar so the readout follows a layout switch (the viewport is one
// shared instance reparented across panes).
function wireCoords(vctx, vp) {
  if (!vp) return;
  const el = vctx.pane.querySelector('[data-cadv-coords]');
  vp.onHoverMove = (p) => {
    if (!el) return;
    if (!p) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = fmtCoords(vp, p);
  };
}

// Two-click distance measurement using the engine's dormant setMeasureLabels layer.
// A third click starts a fresh measurement.
function onMeasurePoint(vp, p) {
  if (_measurePts.length >= 2) { _measurePts = []; vp.clearMeasure(); }
  _measurePts.push(p);
  if (_measurePts.length === 1) {
    vp.setMeasureLabels([{ pos: [p.x, p.y, p.z], text: '×' }]);
    return;
  }
  const a = _measurePts[0], b = _measurePts[1];
  const d = Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
  const u = vp.docUnits();
  vp.setMeasureLabels([{
    pos: [(a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2],
    text: vp.fmtValue(d) + (u ? ' ' + u : ''),
  }]);
  vp.drawMeasureLine(a, b);
}

function refreshScaleReadout(vctx, vp) {
  const el = vctx.pane.querySelector('[data-cadv-scale]');
  if (!el || !vp || typeof vp.scaleInfo !== 'function') return;
  const info = vp.scaleInfo();
  el.textContent = info.text || '';
  el.title = info.text || '';
}

function refreshProjectionLabel(vctx, vp) {
  const btn = vctx.pane.querySelector('[data-cadv-projection]');
  if (!btn || !vp) return;
  const ortho = vp.camType === 'ortho';
  btn.textContent = ortho ? 'Ortho' : 'Persp';
  btn.title = ortho ? 'Switch to perspective projection' : 'Switch to orthographic projection';
  btn.setAttribute('aria-pressed', ortho ? 'true' : 'false');
}

// The view segments highlight the last-picked orientation. Orbiting invalidates
// it, so a drag on the canvas clears the highlight (it must never lie).
function clearActiveView(bar) {
  bar.querySelectorAll('[data-cadv-view]').forEach((b) => {
    b.classList.remove('active');
    b.setAttribute('aria-pressed', 'false');
  });
}
function wireOrbitClear(vctx, bar) {
  const host = vctx.pane.querySelector('[data-cad-3d-host]');
  if (!host || host._viewClearWired) return;
  host._viewClearWired = true;
  let sx = 0, sy = 0, down = false;
  host.addEventListener('pointerdown', (e) => { down = true; sx = e.clientX; sy = e.clientY; });
  host.addEventListener('pointermove', (e) => {
    if (down && (Math.abs(e.clientX - sx) > 4 || Math.abs(e.clientY - sy) > 4)) { clearActiveView(bar); down = false; }
  });
  host.addEventListener('pointerup', () => { down = false; });
}

function wireViewbar(vctx, vp) {
  const bar = vctx.pane.querySelector('[data-cadv-viewbar]');
  wireCoords(vctx, vp);   // (re)bind the live-coord readout to the current pane, always
  if (!bar || bar._wired || !vp) { refreshProjectionLabel(vctx, vp); refreshScaleReadout(vctx, vp); return; }
  bar._wired = true;
  wireOrbitClear(vctx, bar);
  bar.querySelectorAll('[data-cadv-view]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (vctx.store.viewport) {
        vctx.store.viewport.setView(btn.dataset.cadvView);
        clearActiveView(bar);
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        refreshScaleReadout(vctx, vctx.store.viewport);
      }
    });
  });
  const projection = bar.querySelector('[data-cadv-projection]');
  if (projection) {
    projection.addEventListener('click', () => {
      const current = vctx.store.viewport;
      if (!current) return;
      current.toggleOrtho();
      refreshProjectionLabel(vctx, current);
      refreshScaleReadout(vctx, current);
    });
  }
  const frame = bar.querySelector('[data-cadv-frame]');
  if (frame) {
    frame.addEventListener('click', () => {
      if (vctx.store.viewport) {
        vctx.store.viewport.frameSelected();
        refreshScaleReadout(vctx, vctx.store.viewport);
      }
    });
  }
  const measure = bar.querySelector('[data-cadv-measure]');
  if (measure) {
    measure.addEventListener('click', () => {
      const current = vctx.store.viewport;
      if (!current) return;
      const on = !measure.classList.contains('active');
      measure.classList.toggle('active', on);
      measure.setAttribute('aria-pressed', on ? 'true' : 'false');
      _measurePts = [];
      current.clearMeasure();
      current.setMeasureMode(on);
      if (on) {
        current.onMeasurePoint = (p) => onMeasurePoint(current, p);
        if (vctx.setStatus) vctx.setStatus('Measure: click two points');
      }
    });
  }
  refreshProjectionLabel(vctx, vp);
  refreshScaleReadout(vctx, vp);
}

const _v = defineView({
  id: 'viewport-3d',
  styles: STYLES,
  markup: `<div class="cadv-3d-host" data-cad-3d-host>
    <div class="cadv-3d-viewbar" data-cadv-viewbar aria-label="3D view controls">
      <div class="eos-tool-segmented" role="group" aria-label="Camera view">
        <button type="button" class="eos-tool-segment" data-cadv-view="iso" title="Isometric view" aria-pressed="false">Iso</button>
        <button type="button" class="eos-tool-segment" data-cadv-view="top" title="Top view" aria-pressed="false">Top</button>
        <button type="button" class="eos-tool-segment" data-cadv-view="front" title="Front view" aria-pressed="false">Front</button>
        <button type="button" class="eos-tool-segment" data-cadv-view="right" title="Right view" aria-pressed="false">Right</button>
      </div>
      <button type="button" class="eos-tool-btn" data-cadv-projection title="Switch projection" aria-pressed="false">Persp</button>
      <button type="button" class="eos-tool-btn" data-cadv-frame title="Frame selected or all">Frame</button>
      <button type="button" class="eos-tool-btn" data-cadv-measure title="Measure distance between two clicked points" aria-pressed="false">Measure</button>
    </div>
    <div class="cadv-3d-scale" data-cadv-scale></div>
    <div class="cadv-3d-coords" data-cadv-coords hidden></div>
  </div>`,
  events: ['doc', 'object', 'select'],

  async mount(vctx) {
    const host = vctx.pane.querySelector('[data-cad-3d-host]') || vctx.pane;
    const store = vctx.store;
    let vp = store.viewport;
    if (!vp) {
      // Lazy single construction on the first 3D pane (init() needs a sized host
      // for the camera aspect, so we build here rather than on a hidden holder).
      const opts = (vctx.config && vctx.config.opts) || {};
      vp = new CadViewport(host, {
        gridSize: opts.gridSize || 400,
        onSelect: (id) => {
          const owner = store.objectOwningFeature ? store.objectOwningFeature(id) : null;
          store.select(owner ? owner.oid : id);
        },
        onCursorMove: (c) => store.setCursor(c.x, c.y, c.z),
        onTransformCommit: (id, t) => {
          // Gizmo drop → write the transform back into the feature source + broadcast.
          const owner = store.objectOwningFeature ? store.objectOwningFeature(id) : null;
          if (owner) {
            if (vctx.setStatus) vctx.setStatus('Generated geometry is read-only. Edit the source object.', true);
            vp.setDocument(store.doc);
            vp.rebuild();
            return;
          }
          const doc = store.doc;
          const f = (doc.features || []).find((ff) => ff.id === id);
          if (f) { if (store.pushUndo) store.pushUndo(); f.at = t.at; f.rotate = t.rotate; store.notifyDoc(); }
        },
        onStatus: (m, e) => { if (vctx.setStatus) vctx.setStatus(m, e); },
      });
      vp.init();
      store.setViewport(vp);
    } else {
      vp.attachTo(host);
    }
    vp.setDocument(store.doc);
    vp.rebuild();
    const selectedId = selectedFeatureId(store);
    if (vp.selectedId !== selectedId) vp.select(selectedId);
    wireViewbar(vctx, vp);
  },

  update(vctx, evt) {
    const vp = vctx.store.viewport;
    if (!vp) return;
    wireViewbar(vctx, vp);
    if (evt.type === 'doc' || evt.type === 'object') {
      vp.setDocument(vctx.store.doc);
      vp.rebuild();
      refreshScaleReadout(vctx, vp);
    } else if (evt.type === 'select') {
      const selectedId = selectedFeatureId(vctx.store);
      if (vp.selectedId !== selectedId) vp.select(selectedId);
    }
  },

  teardown(vctx) {
    const vp = vctx.store.viewport;
    if (vp) {
      // Don't carry measure mode / a stale coord readout across a layout switch —
      // the next pane's viewbar re-wires both from a clean state.
      vp.setMeasureMode(false);   // also clears the labels + line
      _measurePts = [];
      if (vp.onHoverMove) vp.onHoverMove(null);
      vp.parkCanvas();   // park the live canvas; never destroy (no dispose())
    }
  },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
