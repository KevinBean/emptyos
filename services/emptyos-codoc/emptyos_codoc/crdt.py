"""Server-side CRDT merge (Phase 3 foundation) — a pycrdt Y.Doc per live room.

Upgrades the relay from "persist the last pushed frame" to a real conflict-free
merge: the server keeps a Y.Doc per active room, applies each client's Yjs
*update* into it (commutative — order doesn't matter), persists the merged full
state, and bootstraps a late joiner with that full state. This is also the basis
for the agent live co-editor: an agent's pycrdt client applies updates into the
same shared Doc, attributed by principal, exactly like a human's browser.

Thin wrappers over pycrdt so the app code stays decoupled from the binding and a
corrupt/garbage update can never brick a room.
"""

from __future__ import annotations

from pycrdt import Doc


def new_doc(initial: bytes | None = None) -> Doc:
    """A fresh Y.Doc, optionally hydrated from a persisted full-state update.

    Corrupt persisted bytes start an empty doc rather than raising — a bad
    snapshot must not make the room unopenable.
    """
    doc = Doc()
    if initial:
        try:
            doc.apply_update(initial)
        except Exception:
            pass
    return doc


def apply_update(doc: Doc, update: bytes) -> bool:
    """Merge a client update into the room doc. Returns False on an invalid
    update (caller then skips persist + broadcast — garbage never propagates)."""
    try:
        doc.apply_update(update)
        return True
    except Exception:
        return False


def full_state(doc: Doc) -> bytes:
    """The whole doc encoded as one update — for persistence + joiner bootstrap."""
    return doc.get_update()
