// eos-cad-hand-input.js — webcam hand-gesture input for the CAD part editor
// (Phase F of the CadXStudio-parity plan: a differentiator, not a gap-close —
// CadXStudio has no hand/XR input at all).
//
// 100% client-side: MediaPipe Tasks Vision's HandLandmarker runs the model
// via WASM in the browser. No webcam frame is ever sent anywhere — not to
// the EmptyOS daemon, not to any cloud service. Trivially satisfies
// CLAUDE.md Rule 19 (no vault/user data to cloud) because there is no
// network call in this module's hot path at all.
//
// Design choice: reuse the EXISTING store primitives, don't invent new ones.
//   - store.viewport.worldFromClient(x, y) — the same screen->world raycast
//     mouse picking already uses (see eos-cad-viewport.js), given a public
//     entry point so a non-pointer input source doesn't have to reach into
//     private members.
//   - store.viewport.setCursor(x, y, z) — the same 3D cursor the "Cursor->0
//     / Cursor->sel / Sel->cursor" buttons already drive; it fans out to
//     store.setCursor via the wired onCursorMove callback, so calling it
//     once is enough — never call store.setCursor directly too.
//   - store.pushUndo() once at pinch-START, store.notifyDoc() while
//     dragging — the exact pattern part-tools.js's selectedToCursor() uses
//     for a single cursor-snap, generalised to a continuous drag.
//
// Gesture vocabulary (v1, single hand, move-only — two-hand rotate/scale is
// a deferred stretch goal, see docs/CAD-ROADMAP.md-adjacent plan notes):
//   pinch (thumb tip near index tip) + a feature already selected = grab and
//   drag it to wherever the index fingertip points; release = drop. No pinch
//   = idle hover, the cursor still follows the hand but nothing moves.
//
// Failure modes all degrade to a status message; the mouse/keyboard/gizmo
// path is completely unaffected either way — this is purely additive.

const WASM_BASE = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.17/wasm';
// The 'float16' variant is the smallest/fastest official model; accuracy is
// ample for pinch/point gestures (this isn't sign-language recognition).
const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';

// MediaPipe's 21-point hand landmark indices (fixed by the model, not ours).
const THUMB_TIP = 4;
const INDEX_TIP = 8;

const PINCH_THRESHOLD = 0.06;      // normalized landmark-space distance
const DRAG_COMMIT_MS = 80;         // throttle notifyDoc() during a drag — a
                                    // boolean-heavy part's CSG rebuild is not
                                    // free; ~12/s is plenty responsive without
                                    // being pathological on a complex tree.

export function createHandInput({ store, onStatus } = {}) {
  let landmarker = null;
  let video = null;
  let stream = null;
  let rafId = null;
  let active = false;
  let pinching = false;
  let grippedId = null;
  let lastCommit = 0;

  const status = (msg, isErr) => { if (onStatus) onStatus(msg, !!isErr); };

  async function ensureLandmarker() {
    if (landmarker) return landmarker;
    status('Loading hand-tracking model…');
    const { HandLandmarker, FilesetResolver } = await import('@mediapipe/tasks-vision');
    const fileset = await FilesetResolver.forVisionTasks(WASM_BASE);
    landmarker = await HandLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
      runningMode: 'VIDEO',
      numHands: 1,
    });
    return landmarker;
  }

  function dist2(a, b) {
    const dx = a.x - b.x, dy = a.y - b.y;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function endGrip(msg) {
    pinching = false;
    grippedId = null;
    status(msg);
  }

  function tick() {
    if (!active) return;
    rafId = requestAnimationFrame(tick);
    if (!video || video.readyState < 2 || !landmarker) return;
    const vp = store.viewport;
    // Exact B-rep view mode is read-only inspection (mirrors _onPick's own
    // policy) — hand input is inert there too, not a second exception.
    if (!vp || vp.viewMode === 'exact') return;

    let result;
    try {
      result = landmarker.detectForVideo(video, performance.now());
    } catch (e) {
      return;  // a transient decode hiccup — try again next frame
    }
    const hand = result && result.landmarks && result.landmarks[0];
    if (!hand) {
      if (pinching) endGrip('No hand detected — released');
      else status('No hand detected');
      return;
    }

    // Mirror X — a front-facing "selfie" feed reads naturally as a mirror,
    // but reaching INTO the scene should feel like a real reach, not one.
    const idx = hand[INDEX_TIP];
    const rect = vp.domElement.getBoundingClientRect();
    const clientX = rect.left + (1 - idx.x) * rect.width;
    const clientY = rect.top + idx.y * rect.height;
    const world = vp.worldFromClient(clientX, clientY);
    if (world) vp.setCursor(world.x, world.y, world.z);   // fans out to store.setCursor

    const isPinched = dist2(hand[THUMB_TIP], hand[INDEX_TIP]) < PINCH_THRESHOLD;
    if (isPinched && !pinching) {
      pinching = true;
      grippedId = store.selection || null;
      if (grippedId) store.pushUndo();   // one snapshot for the whole gesture
      status(grippedId ? 'Grabbed — move your hand to drag it' : 'Pinched, but nothing is selected');
    } else if (!isPinched && pinching) {
      endGrip('Released');
    }

    if (pinching && grippedId && world) {
      const now = performance.now();
      if (now - lastCommit < DRAG_COMMIT_MS) return;
      lastCommit = now;
      const f = (store.doc.features || []).find((x) => x.id === grippedId);
      if (f) {
        f.at = [Math.round(world.x), Math.round(world.y), Math.round(world.z)];
        store.notifyDoc();
      }
    } else if (!pinching) {
      status('Tracking — select a feature, then pinch to grab it');
    }
  }

  async function start() {
    if (active) return { ok: true };
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status('Camera access is not available in this browser.', true);
      return { ok: false, error: 'getUserMedia unavailable' };
    }
    try {
      await ensureLandmarker();
    } catch (e) {
      status('Could not load the hand-tracking model: ' + e, true);
      return { ok: false, error: String(e) };
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: 480, height: 360 }, audio: false });
    } catch (e) {
      status('Camera permission denied, or no camera available.', true);
      return { ok: false, error: String(e) };
    }
    video = document.createElement('video');
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await video.play();
    active = true;
    lastCommit = 0;
    status('Tracking — select a feature, then pinch to grab it');
    tick();
    return { ok: true };
  }

  function stop() {
    active = false;
    pinching = false;
    grippedId = null;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    if (video) { video.srcObject = null; video = null; }
    status('');
  }

  return { start, stop, isActive: () => active };
}
