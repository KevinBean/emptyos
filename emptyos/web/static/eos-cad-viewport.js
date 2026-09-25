// CadViewport — the reusable Three.js viewport for the eos-cad/1 feature tree.
//
// Extracted from cad/pages/index.html (was a 1041-line monolith) so the 3D
// renderer + scene viewport can be reused by a future engineering-scene layer
// without dragging the document/UI shell along. Engine-neutral: it consumes the
// same `eos-cad/1` document (`{ params, features }`) that apps/cad/shared.py
// defines and apps/cad/compile_cq.py compiles to exact B-rep.
//
// The PAGE owns document state, DOM panels (tree/params/inspector/overlay),
// persistence, AI drafting, and the Blender modal transform. This module owns
// everything Three.js: scene/camera/lights/grid/gizmo/picking, mesh-from-tree
// rebuild, view presets, and the exact-B-rep preview overlay. It talks back to
// the page only through the four constructor callbacks.
//
// Shared static module — served at /static/eos-cad-viewport.js regardless of
// which apps are installed. Promoted from apps/cad/pages/ once a second consumer
// arrived (engineering-scene), and after the app-local path bit: with the cad app
// disabled, /cad/pages/cad-viewport.js 404'd and broke the scene's import chain.
// Consumers: apps/cad + apps/extension/engineering/engineering-scene. Bare `three`
// specifiers resolve against the importing page's importmap (document-scoped), so
// each consumer page must keep the three / three-bvh-csg importmap in its head.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { Brush, Evaluator, ADDITION, SUBTRACTION, INTERSECTION } from 'three-bvh-csg';

export const DEFAULT_COLOR = '#9aa7b4';

// ── Pure helpers (also imported by the page) ──
export const rad = d => (d || 0) * Math.PI / 180;
export const deg = r => Math.round((r * 180 / Math.PI) * 100) / 100;
export const round = n => Math.round((n || 0) * 1000) / 1000;

export function resolve(doc, v, _seen) {
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    const params = (doc && doc.params) || {};
    const s = v.trim();
    if (s in params) {
      // Cyclic reference (a→b→a) would recurse forever in the browser; mirror
      // shared.py resolve_value's guard and degrade to 0 (the server validator
      // surfaces the cycle so the user can fix it).
      if (!_seen) _seen = new Set();
      if (_seen.has(s)) return 0;
      _seen.add(s);
      return resolve(doc, params[s], _seen);
    }
    const n = parseFloat(s); return isNaN(n) ? 0 : n;
  }
  return 0;
}
export function resolveVec3(doc, v, def = [0, 0, 0]) {
  if (!Array.isArray(v) || v.length < 3) return def.slice();
  return [resolve(doc, v[0]), resolve(doc, v[1]), resolve(doc, v[2])];
}
export function byId(doc) { const m = {}; (doc.features || []).forEach(f => { m[f.id] = f; }); return m; }
export function enabledFeat(f) { return f && f.enabled !== false; }
export function consumedIds(doc) {
  const s = new Set(); const map = byId(doc);
  (doc.features || []).forEach(f => {
    if (!enabledFeat(f)) return;       // a disabled boolean doesn't consume its inputs
    if (f.op === 'union' || f.op === 'intersect') (f.inputs || []).forEach(i => { if (enabledFeat(map[i])) s.add(i); });
    if (f.op === 'subtract') { if (f.target && enabledFeat(map[f.target])) s.add(f.target); (f.tools || []).forEach(i => { if (enabledFeat(map[i])) s.add(i); }); }
  });
  return s;
}
export function b64ToBytes(b64) {
  const bin = atob(b64), arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

// Inject the floating-label pill style once per document. CSS2DRenderer owns the
// element transform (translate(-50%,-50%) + projected offset) — do NOT set
// transform here. Theme tokens with dark-viewport fallbacks.
function _injectLabelStyleOnce() {
  if (typeof document === 'undefined' || document.getElementById('eos-cad-label-style')) return;
  const s = document.createElement('style');
  s.id = 'eos-cad-label-style';
  s.textContent =
    `.eos-cad-label { background: rgba(13,17,23,.85); color: var(--text, #d6deeb);
       border: 1px solid var(--border, #2b3440); border-radius: 4px; padding: 1px 5px;
       font: 11px/1.4 var(--mono, ui-monospace, monospace); white-space: nowrap;
       pointer-events: none; user-select: none; }
     .eos-cad-label.dim { color: var(--muted, #8b98a8); }`;
  document.head.appendChild(s);
}


export class CadViewport {
  constructor(hostEl, opts = {}) {
    this.host = hostEl;
    this.gridSize = opts.gridSize || 400;
    this.gridInfo = { size: this.gridSize, step: Math.max(1, this.gridSize / 10), divisions: Math.max(4, Math.round(this.gridSize / 10)) };
    this.modelBounds = { empty: true, center: new THREE.Vector3(), size: new THREE.Vector3(), maxExtent: 0 };
    this._docRef = null;
    this._needsAutoFrame = true;
    this._autoFrameEdits = true;
    this._cameraFitRadius = 320;
    this.onSelect = opts.onSelect || (() => {});
    this.onTransformCommit = opts.onTransformCommit || (() => {});
    this.onCursorMove = opts.onCursorMove || (() => {});
    // Live hover coordinate (fires on pointermove; null on leave) — kept SEPARATE
    // from onCursorMove so the placed-3D-cursor semantics are untouched. Reassigned
    // per-pane by the view so the readout follows a layout switch.
    this.onHoverMove = opts.onHoverMove || (() => {});
    this._hoverRAF = false; this._hoverEv = null;
    // Measure tool: when measureMode is on, a plain pick reports the world point
    // via onMeasurePoint instead of selecting. The connecting line is engine-owned.
    this.onMeasurePoint = opts.onMeasurePoint || (() => {});
    this.measureMode = false; this._measureLine = null;
    this.onStatus = opts.onStatus || (() => {});

    this.doc = { params: {}, features: [] };
    this.selectedId = null;
    this.camType = 'persp';
    this.viewMode = 'preview';           // 'preview' = editable CSG · 'exact' = compiled B-rep STL
    this.snap = { on: false, step: 1 };

    this.evaluator = new Evaluator();
    this.Z_PLANE = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
    this.cursor3d = new THREE.Vector3(0, 0, 0);

    // Three handles populated in init()
    this.scene = this.camera = this.renderer = this.orbit = this.gizmo = null;
    this.raycaster = this.group = this.exactGroup = this.csgMat = this.cursorHelper = this.grid = null;
    this.domElement = null;
    // Optional floating-label (CSS2D) overlay — generic measurement/annotation
    // layer any consumer can drive via setMeasureLabels(); rendered each frame so
    // it reprojects automatically on orbit / view-preset / ortho-toggle / resize.
    this.css2d = this.labelGroup = null;
  }

  // ── Scene lifecycle ──
  _makeCamera(type) {
    const aspect = this.host.clientWidth / this.host.clientHeight;
    let cam;
    if (type === 'ortho') { const f = 140; cam = new THREE.OrthographicCamera(-f * aspect, f * aspect, f, -f, 0.1, 8000); }
    else cam = new THREE.PerspectiveCamera(50, aspect, 0.1, 8000);
    cam.up.set(0, 0, 1);                       // Z-up (engineering / cadquery convention)
    return cam;
  }

  init() {
    const host = this.host;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d1117);

    this.camera = this._makeCamera('persp');
    this.camera.position.set(160, -200, 140);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.setSize(host.clientWidth, host.clientHeight);
    host.appendChild(this.renderer.domElement);
    this.domElement = this.renderer.domElement;

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.1));
    const dir = new THREE.DirectionalLight(0xffffff, 1.4);
    dir.position.set(120, -160, 220); this.scene.add(dir);

    this._updateAdaptiveGrid();
    this.scene.add(new THREE.AxesHelper(40));

    this.orbit = new OrbitControls(this.camera, this.renderer.domElement);
    this.orbit.enableDamping = true;
    this.orbit.addEventListener('start', () => { this._autoFrameEdits = false; });

    this.gizmo = new TransformControls(this.camera, this.renderer.domElement);
    this.gizmo.setSpace('world');
    this.gizmo.addEventListener('dragging-changed', e => { this.orbit.enabled = !e.value; });
    this.gizmo.addEventListener('mouseUp', () => this._commitTransform());
    this.scene.add(this.gizmo);

    this.csgMat = new THREE.MeshStandardMaterial({ color: DEFAULT_COLOR, metalness: 0.1, roughness: 0.7 });
    this.group = new THREE.Group(); this.scene.add(this.group);
    this.exactGroup = new THREE.Group(); this.exactGroup.visible = false; this.scene.add(this.exactGroup);
    this.raycaster = new THREE.Raycaster();

    // 3D cursor visual (Blender-style reference point)
    this.cursorHelper = new THREE.Group();
    const cl = (c, a, b) => { const g = new THREE.BufferGeometry().setFromPoints([a, b]); return new THREE.Line(g, new THREE.LineBasicMaterial({ color: c })); };
    this.cursorHelper.add(cl(0xff5555, new THREE.Vector3(-8, 0, 0), new THREE.Vector3(8, 0, 0)));
    this.cursorHelper.add(cl(0x55ff7f, new THREE.Vector3(0, -8, 0), new THREE.Vector3(0, 8, 0)));
    this.cursorHelper.add(cl(0x5599ff, new THREE.Vector3(0, 0, -8), new THREE.Vector3(0, 0, 8)));
    this.scene.add(this.cursorHelper);

    // Floating-label overlay: a CSS2DRenderer layered over the WebGL canvas.
    // Rendering it in the loop (below) reprojects labels every frame, so camera
    // changes need no callbacks; only resize must keep its size in sync.
    _injectLabelStyleOnce();
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    this.css2d = new CSS2DRenderer();
    this.css2d.setSize(host.clientWidth, host.clientHeight);
    const ld = this.css2d.domElement;
    ld.style.position = 'absolute'; ld.style.top = '0'; ld.style.left = '0';
    ld.style.pointerEvents = 'none';   // never intercept orbit / pick
    host.appendChild(ld);
    this.labelGroup = new THREE.Group(); this.scene.add(this.labelGroup);

    this.renderer.domElement.addEventListener('pointerdown', ev => this._onPick(ev));
    this.renderer.domElement.addEventListener('pointermove', ev => this._onHover(ev));
    this.renderer.domElement.addEventListener('pointerleave', () => this.onHoverMove(null));
    window.addEventListener('resize', () => this._onResize());

    const loop = () => {
      requestAnimationFrame(loop);
      this.orbit.update();
      this.renderer.render(this.scene, this.camera);
      if (this.css2d) this.css2d.render(this.scene, this.camera);
    };
    loop();
  }

  _onResize() {
    const host = this.host;
    this._adaptCameraToRadius(this._cameraFitRadius || this._currentFrameRadius());
    this.renderer.setSize(host.clientWidth, host.clientHeight);
    if (this.css2d) this.css2d.setSize(host.clientWidth, host.clientHeight);
  }

  // ── Reparenting across layouts (the CAD architecture-rethink spine) ──
  // The GL context + forever-rAF loop + window-resize listener are created ONCE in
  // init() and never torn down (there is deliberately no dispose()). attachTo /
  // parkCanvas let a SINGLE viewport move between panes when the active layout
  // changes, instead of the old full-page navigation that existed only to avoid
  // leaking a second viewport. Additive: init() and every legacy single-host
  // consumer are untouched. (Named parkCanvas, not detach — `detach()` is already
  // the gizmo-detach method below.) See .claude/rules/cad-layouts.md.
  attachTo(hostEl) {
    if (!hostEl || hostEl === this.host) return;          // idempotent
    this.host = hostEl;
    if (getComputedStyle(hostEl).position === 'static') hostEl.style.position = 'relative';
    if (this.renderer && this.renderer.domElement) hostEl.appendChild(this.renderer.domElement);
    if (this.css2d && this.css2d.domElement) hostEl.appendChild(this.css2d.domElement);
    this._onResize();                                     // fix aspect + size for the new pane
  }

  parkCanvas() {
    // Park the canvas + label layer in a hidden 1×1 holder so the forever-rAF loop
    // keeps rendering harmlessly — no GL teardown, the context survives the switch.
    let holder = this._detachHolder;
    if (!holder) {
      holder = document.createElement('div');
      holder.setAttribute('data-cad-viewport-parked', '');
      holder.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;overflow:hidden;';
      (document.body || document.documentElement).appendChild(holder);
      this._detachHolder = holder;
    }
    this.host = holder;
    if (this.renderer && this.renderer.domElement) holder.appendChild(this.renderer.domElement);
    if (this.css2d && this.css2d.domElement) holder.appendChild(this.css2d.domElement);
  }

  // ── Floating dimension/annotation labels (generic; opt-in per consumer) ──
  // labels: [{ pos: [x,y,z], text, className? }]. Replaces any current labels.
  // Feature-detected by callers, so an older viewport simply renders no labels.
  setMeasureLabels(labels) {
    this.clearMeasureLabels();
    if (!this.labelGroup) return;
    (labels || []).forEach(l => {
      const div = document.createElement('div');
      div.className = 'eos-cad-label' + (l.className ? ' ' + l.className : '');
      div.textContent = l.text == null ? '' : String(l.text);
      const obj = new CSS2DObject(div);
      const p = l.pos || [0, 0, 0];
      obj.position.set(p[0] || 0, p[1] || 0, p[2] || 0);
      this.labelGroup.add(obj);
    });
  }

  clearMeasureLabels() {
    if (!this.labelGroup) return;
    [...this.labelGroup.children].forEach(o => {
      this.labelGroup.remove(o);
      if (o.element && o.element.remove) o.element.remove();   // drop the DOM node too
    });
  }

  // ── Document → meshes ──
  setDocument(doc) {
    const next = doc || { params: {}, features: [] };
    if (next !== this._docRef) {
      this._docRef = next;
      this._needsAutoFrame = true;
      this._autoFrameEdits = true;
    }
    this.doc = next;
  }

  _orientedCentered(doc, f) {
    const op = f.op;
    let g;
    if (op === 'box') { const s = resolveVec3(doc, f.size, [10, 10, 10]); g = new THREE.BoxGeometry(s[0] || 1, s[1] || 1, s[2] || 1); }
    else if (op === 'sphere') { g = new THREE.SphereGeometry(resolve(doc, f.r) || 5, 32, 18); }
    else if (op === 'cylinder' || op === 'cone') {
      const h = resolve(doc, f.h) || 10, r = resolve(doc, f.r) || 5;
      const r2 = op === 'cone' ? (resolve(doc, f.r2) || 0) : r;
      g = new THREE.CylinderGeometry(r2, r, h, 48);   // CylinderGeometry(top, bottom, height) — Y axis
      const axis = f.axis || 'z';
      if (axis === 'z') g.rotateX(Math.PI / 2);
      else if (axis === 'x') g.rotateZ(Math.PI / 2);
    } else { g = new THREE.BoxGeometry(10, 10, 10); }
    return g;
  }
  _bakeTransform(doc, g, f) {
    const e = f.rotate || [0, 0, 0];
    if (e[0]) g.rotateX(rad(e[0])); if (e[1]) g.rotateY(rad(e[1])); if (e[2]) g.rotateZ(rad(e[2]));
    const at = resolveVec3(doc, f.at);
    g.translate(at[0], at[1], at[2]);
    return g;
  }
  _geomForRender(doc, id, map) {
    const f = map[id]; if (!f) return null;
    if (f.op === 'box' || f.op === 'sphere' || f.op === 'cylinder' || f.op === 'cone') return this._orientedCentered(doc, f);
    return this._csgCombine(doc, f, map);     // boolean — may be null
  }
  _geomForCombine(doc, id, map) {
    const g = this._geomForRender(doc, id, map); if (!g) return null;
    return this._bakeTransform(doc, g, map[id]);     // bake this feature's own transform for use as a CSG operand
  }
  _csgCombine(doc, f, map) {
    const en = id => enabledFeat(map[id]);
    const brush = id => { const g = this._geomForCombine(doc, id, map); if (!g) return null; const b = new Brush(g, this.csgMat); b.updateMatrixWorld(); return b; };
    try {
      let res = null;
      if (f.op === 'subtract') {
        if (!en(f.target)) return null;
        res = brush(f.target); if (!res) return null;
        (f.tools || []).forEach(t => { if (en(t)) { const b = brush(t); if (b) res = this.evaluator.evaluate(res, b, SUBTRACTION); } });
      } else {
        const ids = (f.inputs || []).filter(en);
        if (!ids.length) return null;
        res = brush(ids[0]); if (!res) return null;
        const OP = f.op === 'intersect' ? INTERSECTION : ADDITION;
        for (let i = 1; i < ids.length; i++) { const b = brush(ids[i]); if (b) res = this.evaluator.evaluate(res, b, OP); }
      }
      return res ? res.geometry : null;
    } catch (err) {
      this.onStatus('Boolean failed for ' + f.id + ': ' + err.message, true);
      return null;
    }
  }

  rebuild() {
    if (!this.group) return;
    [...this.group.children].forEach(m => { this.group.remove(m); m.geometry && m.geometry.dispose(); });
    this.detach(true);
    const doc = this.doc;
    const map = byId(doc);
    const consumed = consumedIds(doc);
    (doc.features || []).forEach(f => {
      if (!enabledFeat(f) || consumed.has(f.id)) return;
      let g; try { g = this._geomForRender(doc, f.id, map); } catch (e) { return; }
      if (!g) return;
      const opacity = f.opacity == null ? 1 : Math.max(0, Math.min(1, Number(f.opacity)));
      const mat = new THREE.MeshStandardMaterial({
        color: f.color || DEFAULT_COLOR, metalness: 0.1, roughness: 0.7,
        opacity, transparent: opacity < 1, depthWrite: opacity >= 1,
        wireframe: !!f.wireframe,
      });
      const mesh = new THREE.Mesh(g, mat);
      const isBool = (f.op === 'union' || f.op === 'subtract' || f.op === 'intersect');
      if (!isBool || f.at || f.rotate) {
        const at = resolveVec3(doc, f.at); mesh.position.set(at[0], at[1], at[2]);
        const e = f.rotate || [0, 0, 0]; mesh.rotation.set(rad(e[0]), rad(e[1]), rad(e[2]));
      }
      mesh.userData.featureId = f.id;
      this.group.add(mesh);
    });
    if (this.selectedId) { const m = this.meshFor(this.selectedId); if (m) this.gizmo.attach(m); }
    this.cursorHelper.position.copy(this.cursor3d);
    this._updateModelBounds();
    this._updateAdaptiveGrid(this.modelBounds);
    if ((this._needsAutoFrame || this._autoFrameEdits) && !this.modelBounds.empty && this.frameAll(true)) {
      this._needsAutoFrame = false;
    }
  }

  // ── Selection + gizmo ──
  meshFor(id) { return this.group.children.find(m => m.userData.featureId === id); }
  select(id) {
    this.selectedId = id;
    const m = this.meshFor(id);
    if (m) this.gizmo.attach(m); else this.gizmo.detach();
    this.onSelect(id);
  }
  detach(keepSel) {
    this.gizmo.detach();
    if (!keepSel) { this.selectedId = null; this.onSelect(null); }
  }
  setGizmoMode(mode) { this.gizmo.setMode(mode); }
  setOrbitEnabled(b) { if (this.orbit) this.orbit.enabled = b; }

  // ── Readout formatting (public: consumers render coords / distances in doc units) ──
  fmtValue(n) { return this._fmtUnitValue(n); }
  docUnits() { return (this.doc && this.doc.units) ? this.doc.units : ''; }

  // ── Measure tool (uses the dormant setMeasureLabels + a picked world point) ──
  setMeasureMode(on) { this.measureMode = !!on; if (!on) this.clearMeasure(); }
  clearMeasure() { this.clearMeasureLabels(); this._clearMeasureLine(); }
  drawMeasureLine(a, b) {
    this._clearMeasureLine();
    if (!a || !b || !this.scene) return;
    const g = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(a.x, a.y, a.z), new THREE.Vector3(b.x, b.y, b.z),
    ]);
    // depthTest off + late renderOrder: a measurement overlay must draw ON TOP of
    // the solid it measures, or the line vanishes inside the geometry.
    this._measureLine = new THREE.Line(g, new THREE.LineBasicMaterial({
      color: 0xffb454, depthTest: false, depthWrite: false,
    }));
    this._measureLine.renderOrder = 999;
    this.scene.add(this._measureLine);
  }
  _clearMeasureLine() {
    if (this._measureLine && this.scene) {
      this.scene.remove(this._measureLine);
      if (this._measureLine.geometry) this._measureLine.geometry.dispose();
    }
    this._measureLine = null;
  }

  // Raycast the pointer to a world point: nearest geometry hit, else the Z=0 plane
  // (null when the ray misses the plane). Shared by hover-readout + measure pick.
  _pointerWorld(ev) {
    return this.worldFromClient(ev.clientX, ev.clientY);
  }

  // Public: screen (client-space, same units as PointerEvent.clientX/Y) -> a
  // world position — raycasts against the visible scene, falling back to the
  // ground plane. The primitive any INPUT SOURCE needs to place the 3D cursor
  // from a 2D point; mouse events use it via `_pointerWorld`, and it's the
  // intended entry point for a non-pointer source (e.g. hand-tracking,
  // eos-cad-hand-input.js) rather than reaching into `_pointerWorld`'s event
  // shape or any other private member.
  worldFromClient(clientX, clientY) {
    if (!this.renderer || !this.raycaster) return null;
    const r = this.renderer.domElement.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const mouse = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(mouse, this.camera);
    const hits = this.group ? this.raycaster.intersectObjects(this.group.children, false) : [];
    if (hits.length) return hits[0].point.clone();
    const p = new THREE.Vector3();
    return this.raycaster.ray.intersectPlane(this.Z_PLANE, p) ? p : null;
  }

  // Throttled (one raycast per animation frame) live coordinate readout. Idle when
  // the pointer is still, so the cost is bounded by frame rate during motion only.
  _onHover(ev) {
    if (this.viewMode === 'exact') return;
    this._hoverEv = ev;
    if (this._hoverRAF) return;
    this._hoverRAF = true;
    requestAnimationFrame(() => {
      this._hoverRAF = false;
      const e = this._hoverEv; if (!e) return;
      this.onHoverMove(this._pointerWorld(e));
    });
  }

  _onPick(ev) {
    if (this.viewMode === 'exact') return;   // exact B-rep is read-only inspection
    if (this.measureMode && !ev.shiftKey) {  // measure mode: report a point, don't select
      const p = this._pointerWorld(ev);
      if (p) this.onMeasurePoint(p);
      return;
    }
    if (ev.shiftKey) {           // Shift+click places the 3D cursor (Blender)
      const p = this._pointerWorld(ev);
      if (p) { this.cursor3d.set(this.snapVal(round(p.x)), this.snapVal(round(p.y)), this.snapVal(round(p.z))); this.cursorHelper.position.copy(this.cursor3d); this.onCursorMove(this.cursor3d); }
      return;
    }
    const r = this.renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(mouse, this.camera);
    const hits = this.raycaster.intersectObjects(this.group.children, false);
    if (hits.length) this.select(hits[0].object.userData.featureId);
  }
  _commitTransform() {
    const m = this.gizmo.object; if (!m) return;
    const id = m.userData.featureId; if (!id) return;
    const at = [this.snapVal(round(m.position.x)), this.snapVal(round(m.position.y)), this.snapVal(round(m.position.z))];
    const rotate = [deg(m.rotation.x), deg(m.rotation.y), deg(m.rotation.z)];
    this.onTransformCommit(id, { at, rotate });
  }

  _visibleFrameGroup() {
    return this.viewMode === 'exact' && this.exactGroup && this.exactGroup.visible
      ? this.exactGroup : this.group;
  }

  _measureGroup(group) {
    const box = new THREE.Box3();
    if (group) box.setFromObject(group);
    if (box.isEmpty()) return { empty: true, box, center: new THREE.Vector3(), size: new THREE.Vector3(), maxExtent: 0 };
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    return { empty: false, box, center, size, maxExtent: Math.max(size.x, size.y, size.z) };
  }

  _updateModelBounds() {
    this.modelBounds = this._measureGroup(this._visibleFrameGroup());
    return this.modelBounds;
  }

  _niceStep(raw) {
    if (!isFinite(raw) || raw <= 0) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / pow;
    const n = f <= 1 ? 1 : (f <= 2 ? 2 : (f <= 5 ? 5 : 10));
    return n * pow;
  }

  _disposeGrid() {
    if (!this.grid || !this.scene) return;
    this.scene.remove(this.grid);
    if (this.grid.geometry) this.grid.geometry.dispose();
    const mats = Array.isArray(this.grid.material) ? this.grid.material : [this.grid.material];
    mats.forEach(m => { if (m && m.dispose) m.dispose(); });
    this.grid = null;
  }

  _updateAdaptiveGrid(bounds) {
    if (!this.scene) return;
    const b = bounds || this.modelBounds || { empty: true, maxExtent: 0 };
    const targetSize = Math.max(this.gridSize, (b.maxExtent || 0) * 2.5);
    const step = this._niceStep(targetSize / 30);
    let divisions = Math.max(4, Math.ceil(targetSize / step));
    if (divisions % 2) divisions += 1;
    const size = Math.max(step * divisions, this.gridSize);
    const unchanged = this.grid && this.gridInfo && this.gridInfo.size === size && this.gridInfo.divisions === divisions;
    this.gridInfo = { size, step, divisions };
    if (unchanged) return;
    this._disposeGrid();
    this.grid = new THREE.GridHelper(size, divisions, 0x3a4452, 0x222b36);
    this.grid.rotation.x = Math.PI / 2;                 // into XY plane for Z-up
    this.scene.add(this.grid);
  }

  _currentFrameRadius() {
    const b = this._measureGroup(this._visibleFrameGroup());
    if (b.empty) return this._cameraFitRadius || 320;
    return b.maxExtent * 1.6 + 1;
  }

  _adaptCameraToRadius(radius) {
    const r = Math.max(1, radius || this._currentFrameRadius());
    const aspect = (this.host.clientWidth || 1) / (this.host.clientHeight || 1);
    this.camera.far = Math.max(8000, r * 4);
    this.camera.near = Math.max(0.1, r * 0.001);
    if (this.camera.isOrthographicCamera) {
      this.camera.left = -r * aspect; this.camera.right = r * aspect;
      this.camera.top = r; this.camera.bottom = -r;
    } else this.camera.aspect = aspect;
    this._cameraFitRadius = r;
    this.camera.updateProjectionMatrix();
  }

  _frameBounds(bounds, auto = false) {
    if (!bounds || bounds.empty) return false;
    if (!auto) this._autoFrameEdits = false;
    const radius = bounds.maxExtent * 1.6 + 1;
    let dirv = this.camera.position.clone().sub(this.orbit.target);
    if (!isFinite(dirv.lengthSq()) || dirv.lengthSq() < 1e-6) dirv = new THREE.Vector3(0.7, -0.7, 0.6);
    dirv.normalize();
    this.orbit.target.copy(bounds.center);
    this.camera.position.copy(bounds.center.clone().add(dirv.multiplyScalar(radius)));
    this._adaptCameraToRadius(radius);
    this.orbit.update();
    return true;
  }

  // ── View presets + ortho toggle (Blender numpad) ──
  setView(name) {
    this._autoFrameEdits = false;
    const target = this.orbit.target.clone();
    const radius = this._currentFrameRadius();
    const currentDist = this.camera.position.distanceTo(target);
    const d = Math.max(radius, currentDist || radius);
    const iso = new THREE.Vector3(0.7, -0.7, 0.6).normalize();
    const dirs = {
      front: new THREE.Vector3(0, -1, 0),
      back: new THREE.Vector3(0, 1, 0),
      right: new THREE.Vector3(1, 0, 0),
      left: new THREE.Vector3(-1, 0, 0),
      top: new THREE.Vector3(0, 0, 1),
      bottom: new THREE.Vector3(0, 0, -1),
      iso,
    };
    const dir = dirs[name] || dirs.iso;
    this.camera.up.copy((name === 'top' || name === 'bottom') ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1));
    this.camera.position.copy(target.clone().add(dir.clone().multiplyScalar(d)));
    this.camera.lookAt(target);
    this._adaptCameraToRadius(Math.max(radius, d));
    this.orbit.target.copy(target);
    this.orbit.update();
  }
  toggleOrtho() {
    this._autoFrameEdits = false;
    const next = this.camType === 'persp' ? 'ortho' : 'persp';
    const pos = this.camera.position.clone(); const tgt = this.orbit.target.clone(); const up = this.camera.up.clone();
    this.camera = this._makeCamera(next); this.camera.position.copy(pos);
    this.camera.up.copy(up);
    this.orbit.dispose(); this.orbit = new OrbitControls(this.camera, this.renderer.domElement); this.orbit.enableDamping = true; this.orbit.target.copy(tgt); this.orbit.update();
    this.gizmo.camera = this.camera; this.camType = next;
    this._adaptCameraToRadius(Math.max(this._currentFrameRadius(), pos.distanceTo(tgt)));
    return this.camType;
  }
  frameSelected() {
    const box = new THREE.Box3();
    const target = this.selectedId ? this.meshFor(this.selectedId) : null;
    this._autoFrameEdits = false;
    if (!target) { this.frameAll(); return; }
    box.setFromObject(target);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    // Adapt the clip planes / ortho frustum to the framed extent via
    // _frameBounds → _adaptCameraToRadius, which sizes near/far each frame from
    // the framed radius against part-scale floors (0.1 / 8000). Framing a
    // metre-scale object (e.g. an 18 m cable run, radius ≈ 28800) otherwise
    // dollies the camera past the 8000 floor and clips the scene to black.
    // Sizing off the radius — NOT the live camera — keeps it idempotent: framing
    // a small part after a large one restores part-scale depth precision instead
    // of inheriting the large run's blown-out range.
    this._frameBounds({ empty: box.isEmpty(), box, center, size, maxExtent: Math.max(size.x, size.y, size.z) });
  }

  frameAll(auto = false) { return this._frameBounds(this._measureGroup(this._visibleFrameGroup()), auto); }

  _fmtUnitValue(n) {
    if (!isFinite(n)) return '0';
    const a = Math.abs(n);
    if (a >= 100 || Number.isInteger(n)) return String(Math.round(n));
    if (a >= 10) return String(Math.round(n * 10) / 10);
    return String(Math.round(n * 100) / 100);
  }

  scaleInfo() {
    const b = this._measureGroup(this._visibleFrameGroup());
    const units = this.doc && this.doc.units ? this.doc.units : 'units';
    const grid = this.gridInfo || { step: 1 };
    const info = { units, gridStep: grid.step, empty: b.empty, extent: null };
    if (!b.empty) info.extent = [b.size.x, b.size.y, b.size.z];
    const parts = [units, 'grid ' + this._fmtUnitValue(grid.step)];
    if (info.extent) parts.push('extent ' + info.extent.map(v => this._fmtUnitValue(v)).join(' x '));
    info.text = parts.join(' · ');
    return info;
  }

  // ── Snap + 3D cursor ──
  setSnap(on, step) {
    this.snap.on = !!on;
    this.snap.step = (isNaN(step) || step <= 0) ? 1 : step;
    this.gizmo.setTranslationSnap(this.snap.on ? this.snap.step : null);
    this.gizmo.setRotationSnap(this.snap.on ? rad(15) : null);
  }
  snapVal(n) { return this.snap.on && this.snap.step > 0 ? Math.round(n / this.snap.step) * this.snap.step : n; }
  get cursor() { return this.cursor3d; }
  setCursor(x, y, z) { this.cursor3d.set(x, y, z); this.cursorHelper.position.copy(this.cursor3d); this.onCursorMove(this.cursor3d); }
  cursorToSelectedMesh() {
    const m = this.selectedId && this.meshFor(this.selectedId);
    if (m) { this.cursor3d.copy(m.position); this.cursorHelper.position.copy(this.cursor3d); this.onCursorMove(this.cursor3d); return true; }
    return false;
  }

  // ── Exact B-rep overlay (CadQuery compile result) ──
  loadExactSTL(b64) {
    const geo = new STLLoader().parse(b64ToBytes(b64).buffer);
    geo.computeVertexNormals();
    [...this.exactGroup.children].forEach(m => { this.exactGroup.remove(m); m.geometry && m.geometry.dispose(); });
    this.exactGroup.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x9aa7b4, metalness: 0.25, roughness: 0.5 })));
    if (this.viewMode === 'exact') {
      this._updateModelBounds();
      this._updateAdaptiveGrid(this.modelBounds);
    }
  }
  setViewMode(mode) {
    this.viewMode = mode;
    const exact = mode === 'exact';
    this.group.visible = !exact;
    this.exactGroup.visible = exact;
    if (exact) this.gizmo.detach(); else if (this.selectedId) { const m = this.meshFor(this.selectedId); if (m) this.gizmo.attach(m); }
    this._updateModelBounds();
    this._updateAdaptiveGrid(this.modelBounds);
  }
  clearExact() {
    if (!this.exactGroup) return;
    [...this.exactGroup.children].forEach(m => { this.exactGroup.remove(m); m.geometry && m.geometry.dispose(); });
    if (this.viewMode === 'exact') this.setViewMode('preview');
  }
  hasExact() { return !!(this.exactGroup && this.exactGroup.children.length); }
}
