"""Co-doc WS wire protocol — a 1-byte type tag in front of each binary frame.

The live WS carries two kinds of message on one socket: Y.Doc CRDT **updates**
(apply + persist + relay) and y-protocols **awareness** updates (presence /
cursors — relay only, never applied to the doc). A single leading byte
distinguishes them so neither path corrupts the other.

  frame = bytes([kind]) + payload

Constants are mirrored verbatim in the browser provider (`web/editor.html`).
"""

from __future__ import annotations

DOC = 0       # payload = a Yjs document update
AWARENESS = 1  # payload = a y-protocols awareness update


def frame(kind: int, payload: bytes) -> bytes:
    return bytes([kind]) + payload


def parse(frame_bytes: bytes) -> tuple[int, bytes]:
    """(kind, payload). An empty frame parses as (DOC, b'') — harmless no-op."""
    if not frame_bytes:
        return DOC, b""
    return frame_bytes[0], frame_bytes[1:]
