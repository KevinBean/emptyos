"""CoDocClient — a programmatic co-editor (the agent live-co-editor mechanism).

This is what an **agent runtime** uses to join a live doc and edit alongside
humans: the *same* CRDT mechanism a human's browser uses (Yjs), in Python, with
every edit attributed to a principal (`agent:<id>`). An agent is a real-time
collaborator, not a special case — its edits merge into the shared Y.Doc exactly
like a human's and are gated by the same membership/level on join.

Protocol (mirrors the browser binding):
  1. join the room WS as the principal (``X-EOS-Principal``)
  2. ``bootstrap(state)`` with the server's merged full state on connect
  3. on a local change, ``insert(...)`` returns the **diff** to send to the server
  4. ``receive(update)`` merges a peer's diff

The diff is a state-vector delta, so it is only valid for a peer that shares the
room's history — hence bootstrap-before-edit. Thin over pycrdt; never raises on a
bad inbound frame.
"""

from __future__ import annotations

from pycrdt import Doc, Text

# The shared text field name — must match the browser binding + the server.
TEXT_FIELD = "t"


class CoDocClient:
    def __init__(self, principal: str):
        self.principal = principal
        self._doc = Doc()
        self._doc[TEXT_FIELD] = Text()

    def bootstrap(self, state: bytes | None) -> None:
        """Apply the server's merged full state on join (shares the history so
        later diffs anchor). A corrupt frame is ignored, not raised."""
        if state:
            try:
                self._doc.apply_update(state)
            except Exception:
                pass

    def receive(self, update: bytes) -> bool:
        """Merge a peer's update. Returns False on an invalid frame (ignored)."""
        try:
            self._doc.apply_update(update)
            return True
        except Exception:
            return False

    def insert(self, index: int, text: str) -> bytes:
        """Apply a local insert and return the diff to send to the server."""
        before = self._doc.get_state()
        self._doc[TEXT_FIELD].insert(index, text)
        return self._doc.get_update(before)

    def append(self, text: str) -> bytes:
        return self.insert(len(self.text), text)

    @property
    def text(self) -> str:
        return str(self._doc[TEXT_FIELD])

    @property
    def state(self) -> bytes:
        """The whole doc as one update — what this client would hand a joiner."""
        return self._doc.get_update()
