// eos-cad-collab.js — real-time multiplayer CAD editing (Phase C of the
// CadXStudio-parity plan). Reuses services/emptyos-codoc's live-collaboration
// relay COMPLETELY UNCHANGED — the server merges opaque binary Yjs updates
// (pycrdt.Doc, verified content-agnostic by reading crdt.py) and never
// inspects the shared-type structure a room's document holds. Today's other
// client (apps/public/standard/codoc/'s text editor) uses Y.Text; this one
// models the eos-cad/1 tree as Y.Map (params) + Y.Array of Y.Map (features)
// instead — same relay, same wire protocol, different document shape.
//
// Deliberately a DIFFERENT mechanism from Phase B's async merge.py: that one
// three-way-merges two point-in-time snapshots via their common ancestor in
// the revision DAG; this one syncs LIVE concurrent edits via CRDT, with no
// notion of "ancestor" at all — every peer's Y.Doc converges to the same
// state regardless of message order or timing. Never blur the two.
//
// Sync strategy: STATE-DIFFING, not intercepted operations. createCadStore()
// mutates store.doc directly in place (e.g. `f.at = [...]; notifyDoc()`), so
// by the time the 'doc' event fires there is no discrete "operation" to
// replay — only "the whole doc, now" vs. "what we last mirrored into Yjs".
// syncStoreIntoYjs() diffs the two and applies only the deltas as individual
// Y.Map.set/delete + Y.Array insert/delete calls inside one ydoc.transact()
// — each becomes its own real CRDT operation with proper causality, so this
// converges exactly like an intercepted-operation sync would. A local-vs-
// remote reentrancy guard (`applyingRemote`) stops the two directions from
// echoing each other.
//
// CLONE EVERY VALUE THAT CROSSES THE doc<->Yjs BOUNDARY. A Y.Map's "Any"
// values (plain arrays/objects like `size`/`at`/`rotate`) are NOT copied on
// set()/get() by the Yjs/pycrdt binding — passing a doc feature's array
// straight into ymap.set() (or straight out via ymap.forEach() into the
// rebuilt store doc) aliases the SAME array object between store.doc and the
// Yjs-tracked value. This codebase's edit convention mutates feature fields
// in place (`f.size[0] = v; notifyDoc()`), so an aliased array means the next
// local edit silently corrupts the Yjs value too, WITHOUT going through
// Map.set() — no CRDT op is produced, nothing is sent, the diff sees no
// difference on its next pass because both sides already changed together.
// Symptom: edits to a feature that arrived via a remote update (or that was
// pushed to Yjs earlier) stop propagating, with no error anywhere. Every
// push (seed/add) and every pull (yjsToDoc) below goes through clone().

const DOC = 0, AWARENESS = 1;
const YJS_CDN = 'https://esm.sh/yjs@13';
const AWARENESS_CDN = 'https://esm.sh/y-protocols@1/awareness';

function jstr(v) { try { return JSON.stringify(v); } catch (e) { return String(v); } }
function clone(v) { try { return JSON.parse(JSON.stringify(v)); } catch (e) { return v; } }

export function createCollabClient({ store, wsUrl, principal, seed, onStatus, onPeers }) {
  let Y = null, AwarenessCtor = null, encodeAwarenessUpdate = null, applyAwarenessUpdate = null;
  let ydoc = null, yparams = null, yfeatures = null, awareness = null;
  let ws = null;
  let active = false;
  let applyingRemote = false;   // set while rebuildStoreFromYjs() is writing store.doc
  let unsubscribeStore = null;

  const status = (msg, isErr) => { if (onStatus) onStatus(msg, !!isErr); };

  async function ensureYjs() {
    if (Y) return;
    const mod = await import(/* webpackIgnore: true */ YJS_CDN);
    Y = mod;
    const aw = await import(/* webpackIgnore: true */ AWARENESS_CDN);
    AwarenessCtor = aw.Awareness;
    encodeAwarenessUpdate = aw.encodeAwarenessUpdate;
    applyAwarenessUpdate = aw.applyAwarenessUpdate;
  }

  function send(kind, payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const msg = new Uint8Array(payload.length + 1);
    msg[0] = kind;
    msg.set(payload, 1);
    ws.send(msg);
  }

  // ── store.doc (plain JSON) <-> Yjs Map/Array, in both directions ────────

  function seedYjsFromDoc(doc) {
    ydoc.transact(() => {
      Object.entries(doc.params || {}).forEach(([k, v]) => yparams.set(k, clone(v)));
      (doc.features || []).forEach((f) => {
        const ymap = new Y.Map();
        Object.entries(f).forEach(([k, v]) => ymap.set(k, clone(v)));
        yfeatures.push([ymap]);
      });
    }, 'local');
  }

  function yjsToDoc() {
    const params = {};
    yparams.forEach((v, k) => { params[k] = clone(v); });
    const features = yfeatures.toArray().map((ymap) => {
      const f = {};
      ymap.forEach((v, k) => { f[k] = clone(v); });
      return f;
    });
    return { ...store.doc, params, features };
  }

  function syncStoreIntoYjs() {
    if (applyingRemote || !active) return;   // don't echo a remote-caused doc event back out
    const doc = store.doc;
    ydoc.transact(() => {
      const params = doc.params || {};
      const seenKeys = new Set(Object.keys(params));
      Object.entries(params).forEach(([k, v]) => { if (jstr(yparams.get(k)) !== jstr(v)) yparams.set(k, clone(v)); });
      yparams.forEach((_, k) => { if (!seenKeys.has(k)) yparams.delete(k); });

      const byId = new Map();
      yfeatures.toArray().forEach((ymap) => byId.set(ymap.get('id'), ymap));
      const liveIds = new Set();
      (doc.features || []).forEach((f) => {
        if (!f || !f.id) return;
        liveIds.add(f.id);
        const existing = byId.get(f.id);
        if (!existing) {
          const ymap = new Y.Map();
          Object.entries(f).forEach(([k, v]) => ymap.set(k, clone(v)));
          yfeatures.push([ymap]);
        } else {
          const cur = {};
          existing.forEach((v, k) => { cur[k] = v; });
          Object.entries(f).forEach(([k, v]) => { if (jstr(cur[k]) !== jstr(v)) existing.set(k, clone(v)); });
          Object.keys(cur).forEach((k) => { if (!(k in f)) existing.delete(k); });
        }
      });
      // Deletions — walk backwards so removing index i doesn't shift the
      // indices of items still to be checked.
      for (let i = yfeatures.length - 1; i >= 0; i--) {
        const id = yfeatures.get(i).get('id');
        if (!liveIds.has(id)) yfeatures.delete(i, 1);
      }
    }, 'local');
  }

  function rebuildStoreFromYjs() {
    applyingRemote = true;
    try {
      store.setDoc(yjsToDoc());   // fires 'doc' -> every existing view repaints, unchanged
    } finally {
      applyingRemote = false;
    }
  }

  // ── wiring ────────────────────────────────────────────────────────────

  function wireYjsObservers() {
    yparams.observeDeep(() => { if (!applyingRemote) rebuildStoreFromYjs(); });
    yfeatures.observeDeep(() => { if (!applyingRemote) rebuildStoreFromYjs(); });
  }

  function wireStoreListener() {
    // createCadStore()'s real subscription API: subscribe(fn, types) ->
    // an unsubscribe function (NOT an on/off pair) — see eos-cad-store.js.
    if (unsubscribeStore || !store.subscribe) return;
    unsubscribeStore = store.subscribe(() => syncStoreIntoYjs(), ['doc']);
  }

  function unwireStoreListener() {
    if (unsubscribeStore) { unsubscribeStore(); unsubscribeStore = null; }
  }

  async function start() {
    if (active) return { ok: true };
    try {
      await ensureYjs();
    } catch (e) {
      status('Could not load the live-collaboration library: ' + e, true);
      return { ok: false, error: String(e) };
    }

    ydoc = new Y.Doc();
    yparams = ydoc.getMap('params');
    yfeatures = ydoc.getArray('features');
    awareness = new AwarenessCtor(ydoc);
    awareness.setLocalStateField('user', { name: principal });
    awareness.on('change', () => {
      const names = [...awareness.getStates().values()].map((s) => s.user && s.user.name).filter(Boolean);
      if (onPeers) onPeers(names);
    });

    return new Promise((resolve) => {
      ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => {
        active = true;
        status('Connected as ' + principal);
        wireYjsObservers();
        wireStoreListener();
        // Seed ONLY when the server told us (via /live/start's `created`
        // flag, threaded through as `seed`) that WE created this room —
        // never by locally guessing "looks empty". Two clients racing to
        // seed a genuinely-empty room would each push a full duplicate copy
        // of every feature (Yjs merges concurrent Y.Array.push, it doesn't
        // deduplicate) — the server-assigned single seeder avoids that
        // entirely. A late joiner just waits for the server's bootstrap frame.
        if (seed && (store.doc.features || []).length > 0) seedYjsFromDoc(store.doc);
        resolve({ ok: true });
      };
      ws.onerror = () => { status('Live session connection error.', true); resolve({ ok: false, error: 'ws error' }); };
      ws.onclose = () => { if (active) status('Disconnected from live session.', true); stop(); };
      ws.onmessage = (e) => {
        const data = new Uint8Array(e.data);
        const kind = data[0];
        const payload = data.subarray(1);
        if (kind === DOC) Y.applyUpdate(ydoc, payload, 'remote');
        else if (kind === AWARENESS) applyAwarenessUpdate(awareness, payload, 'remote');
      };
      ydoc.on('update', (update, origin) => { if (origin !== 'remote') send(DOC, update); });
      awareness.on('update', ({ added, updated, removed }, origin) => {
        if (origin === 'remote') return;
        send(AWARENESS, encodeAwarenessUpdate(awareness, [...added, ...updated, ...removed]));
      });
    });
  }

  function stop() {
    active = false;
    unwireStoreListener();
    if (ws) { try { ws.close(); } catch (e) { /* already closing */ } ws = null; }
    ydoc = null; yparams = null; yfeatures = null; awareness = null;
    status('');
  }

  return { start, stop, isActive: () => active };
}
