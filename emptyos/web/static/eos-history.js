// eos-history.js — shared undo/redo snapshot history for the CAD document stores.
//
// Extracted from the duplicated stacks in eos-cad-store.js (eos-cad/1) and
// eos-draft-store.js (eos-draft/1) — CLAUDE.md rule 9, the 2nd-consumer trigger.
// The two stores differ only in HOW they snapshot + restore (cad clones the whole
// doc and replaces the reference; draft clones a field subset and mutates in place),
// so those two operations are injected; the stack mechanics (cap, redo-clearing,
// the snapshot-current-before-restore dance, the enabled-state signal) are shared.
//
// Dependency-free. `restore` owns its own re-render emit; `onChange({canUndo,canRedo})`
// fires after every push/undo/redo so the store can emit its own 'history' event.

export function createHistory(opts) {
  const snapshot = opts.snapshot;                 // () -> a deep-cloned snapshot value
  const restore = opts.restore;                   // (snap) -> apply it back (+ emit doc)
  const limit = opts.limit || 100;                // max undo depth before the oldest drops
  const onChange = opts.onChange;                 // ({canUndo, canRedo}) -> void, optional
  const undoStack = [];
  const redoStack = [];

  const notify = () => {
    if (onChange) onChange({ canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 });
  };

  return {
    // Snapshot the CURRENT state so the next mutation can be undone. Call BEFORE
    // mutating. Clears redo (a fresh edit invalidates the redo branch).
    push() {
      undoStack.push(snapshot());
      if (undoStack.length > limit) undoStack.shift();
      redoStack.length = 0;
      notify();
    },
    undo() {
      if (!undoStack.length) return false;
      redoStack.push(snapshot());                 // current -> redo (so redo can return here)
      restore(undoStack.pop());                   // a clone from a prior push — unaliased
      notify();
      return true;
    },
    redo() {
      if (!redoStack.length) return false;
      undoStack.push(snapshot());
      restore(redoStack.pop());
      notify();
      return true;
    },
    // Drop all history (e.g. after loading a fresh document — the prior doc's
    // edits are no longer meaningful to undo into).
    clear() {
      undoStack.length = 0;
      redoStack.length = 0;
      notify();
    },
    get canUndo() { return undoStack.length > 0; },
    get canRedo() { return redoStack.length > 0; },
  };
}
