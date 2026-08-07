// eos-cad-live.js — the live-consequence tick for the shared CAD store.
//
// SimCity's third mechanic. The store (eos-cad-store.js) is the one world model and
// defineView gives N synchronized overlays over it — but until this module, nothing
// closed the loop: every engineering check was a manual "Run checks" button, so you
// moved a transformer and the world said nothing back. This attaches the missing
// tick: edit → debounce → cheap staleness probe → expensive recompute only if needed
// → store.setAnalysis(...) → every overlay view repaints.
//
// DOMAIN-FREE BY DESIGN. This module knows nothing about scenes, clearances, or
// endpoints — the caller injects `probe` and `run` closures that hit its OWN app's
// backend. Same discipline as .claude/rules/cad-extensions.md: "never put domain
// backend logic in the cad base app". Adding a second consumer should require no
// change here.
//
// Usage (see eos-cad-views/scene-editor.js for the reference consumer):
//
//   const live = attachLiveConsequence(store, {
//     key:   () => sceneId(),                      // discard results if this changed
//     probe: async () => ({ stale, analysis }),    // cheap: is a recompute needed?
//     run:   async () => analysis,                 // expensive: recompute
//     onStatus: (s, detail) => setStatus(detail),
//   });
//   // ... later, in teardown:
//   live.detach();

const DEFAULT_DEBOUNCE_MS = 400;
const DEFAULT_EVENTS = ['doc', 'object'];

// Status values handed to onStatus, in the order a cycle passes through them.
export const LIVE_IDLE = 'idle';         // nothing scheduled
export const LIVE_CHECKING = 'checking'; // probe in flight
export const LIVE_RUNNING = 'running';   // recompute in flight
export const LIVE_CLEAN = 'clean';       // probe said not stale; nothing to do
export const LIVE_ERROR = 'error';       // probe or run threw / returned nothing

// The minimal store-shaped surface attachLiveConsequence needs: subscribe(fn, mask)
// + setAnalysis. Extracted at the second consumer (a classic-script designer with
// its own `var STATE` and no createCadStore) so a surface
// can adopt the tick WITHOUT adopting the CAD store — which is what "domain-free"
// has to mean to be worth claiming.
//
//   const ch = createAnalysisChannel();
//   attachLiveConsequence(ch, {probe, run, ...});
//   ch.subscribe(() => repaint(ch.analysis), ['analysis']);
//   // then, from your own edit paths:
//   ch.notify('doc');            // "the model changed" — schedules a cycle
//
// Deliberately NOT a general event bus: it carries derived analysis and the one
// signal that invalidates it. A surface needing more already wants createCadStore.
export function createAnalysisChannel() {
  let analysis = null;
  const subs = new Set();
  function emit(type) {
    for (const s of Array.from(subs)) {
      if (s.mask && !s.mask.has(type)) continue;
      try { s.fn({ type }); } catch (e) {
        if (typeof console !== 'undefined' && console.warn) console.warn('[analysis-channel] subscriber failed', e);
      }
    }
  }
  return {
    get analysis() { return analysis; },
    subscribe(fn, types) {
      const s = { fn, mask: (types && types.length) ? new Set(types) : null };
      subs.add(s);
      return () => subs.delete(s);
    },
    setAnalysis(a) { analysis = (a === undefined ? null : a); emit('analysis'); },
    notify(type) { emit(type || 'doc'); },
  };
}

export function attachLiveConsequence(store, opts) {
  const o = opts || {};
  const debounceMs = (typeof o.debounceMs === 'number' && o.debounceMs >= 0)
    ? o.debounceMs : DEFAULT_DEBOUNCE_MS;
  const events = (Array.isArray(o.events) && o.events.length) ? o.events.slice() : DEFAULT_EVENTS;
  const keyOf = (typeof o.key === 'function') ? o.key : () => null;
  const probe = (typeof o.probe === 'function') ? o.probe : null;
  const run = (typeof o.run === 'function') ? o.run : null;
  const onStatus = (typeof o.onStatus === 'function') ? o.onStatus : () => {};

  let timer = null;      // debounce handle
  let busy = false;      // a cycle is in flight (single-flight guard)
  let again = false;     // an event landed mid-cycle — run once more after
  let detached = false;

  function status(s, detail) {
    try { onStatus(s, detail || ''); } catch (e) { /* a status sink must never break the tick */ }
  }

  // One cycle: probe (cheap) → run (expensive) only when the probe says stale.
  //
  // The probe is what makes this safe to fire on every doc event. It is also the
  // loop-breaker: a recompute that itself causes a doc event settles on the next
  // pass, because by then the inputs hash matches and the probe reports clean.
  async function cycle(force) {
    if (detached || busy) { again = true; return; }
    busy = true;
    // Capture the entity this cycle belongs to. If the user switches scenes while
    // we're awaiting, the result describes a document that is no longer loaded —
    // publishing it would paint one scene's violations over another's geometry.
    const token = keyOf();
    try {
      let analysis = null;

      if (probe && !force) {
        status(LIVE_CHECKING, 'Checking…');
        const p = await probe();
        if (detached || keyOf() !== token) return;
        if (!p) { status(LIVE_ERROR, 'Check unavailable'); return; }
        if (!p.stale) {
          // Already current — publish what the probe returned (so a freshly opened
          // document shows its stored result) and skip the expensive path.
          if (p.analysis !== undefined) store.setAnalysis(p.analysis);
          status(LIVE_CLEAN, '');
          return;
        }
        // Show the stale result while the recompute runs, so overlays don't blank out.
        if (p.analysis !== undefined) store.setAnalysis(p.analysis);
      }

      if (!run) { status(LIVE_IDLE, ''); return; }
      status(LIVE_RUNNING, 'Recomputing…');
      analysis = await run();
      if (detached || keyOf() !== token) return;
      if (analysis === null || analysis === undefined) { status(LIVE_ERROR, 'Recompute failed'); return; }
      store.setAnalysis(analysis);
      status(LIVE_IDLE, '');
    } catch (e) {
      if (!detached) status(LIVE_ERROR, (e && e.message) ? e.message : 'Live check failed');
    } finally {
      busy = false;
      if (again && !detached) { again = false; schedule(); }
    }
  }

  function schedule(force) {
    if (detached) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; cycle(force); }, debounceMs);
  }

  // Subscribe to document mutations only. Note `analysis` is NOT in the mask —
  // the tick publishes that event itself, and listening to it would self-trigger.
  const unsub = store.subscribe(() => schedule(false), events);

  if (o.immediate !== false) schedule(false);

  return {
    // Force a recompute now, skipping both the debounce and the staleness probe.
    // This is what a manual "Run checks" button calls once live mode is on.
    runNow() { if (timer) { clearTimeout(timer); timer = null; } return cycle(true); },
    detach() {
      detached = true;
      if (timer) { clearTimeout(timer); timer = null; }
      try { unsub(); } catch (e) { /* already gone */ }
    },
  };
}
