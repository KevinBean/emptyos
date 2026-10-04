"""Co-doc service — ACL-gated real-time relay (Phase-2 foundation).

A Lane-1 HTTP+WebSocket service (no vault, never inside the daemon). It provides
the *live collaboration* layer the single-user daemon never had: a document that
multiple principals open at once, edits relayed in real time, gated by who may
join and at what level. It reuses the Phase-1 trust model — the principal arrives
in the ``X-EOS-Principal`` header, set by the trusted control-plane hop exactly
like ``X-EOS-Actor`` (a browser can't forge it; the service only trusts the hop).

SCOPE (honest): this is the relay + persistence + ACL gate. The CRDT merge logic
lives in the browser's Yjs client (the server relays opaque binary frames); a
server-side ``y-py`` merge — needed for durable conflict-free state + the agent
co-editor (Phase 3) — is the documented next slice. Persistence here stores the
latest client-pushed snapshot frame for late-joiners, not a server-merged Y.Doc.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from . import crdt, protocol
from .snapshot import slugify, snapshot_markdown
from .store import LEVELS, CoDocStore

_WEB = Path(__file__).parent / "web"


def _principal(request: Request) -> str:
    pid = request.headers.get("x-eos-principal", "").strip()
    if not pid:
        raise HTTPException(status_code=401, detail="principal required")
    return pid


def _ws_principal(ws: WebSocket) -> str:
    """Principal for a WebSocket join — trusted-hop header only, by default.

    Production: the trusted control-plane hop injects ``X-EOS-Principal`` on the
    WS upgrade (a browser cannot set custom WS headers, so a client can't forge
    it). A raw client-supplied principal must NEVER be trusted — accepting
    ``?principal=`` from the query string is an identity-spoof (any client could
    claim to be any member).

    Dev only: ``?principal=`` is honoured as a fallback for a direct
    browser→service connection with no proxy, but ONLY when ``CODOC_DEV_WS_AUTH``
    is set (1/true/yes). Off by default → secure by default.
    """
    pid = (ws.headers.get("x-eos-principal") or "").strip()
    if not pid and os.environ.get("CODOC_DEV_WS_AUTH", "").strip().lower() in ("1", "true", "yes"):
        pid = (ws.query_params.get("principal") or "").strip()
    return pid


def create_app(store: CoDocStore | None = None) -> FastAPI:
    # CODOC_DB lets a deploy persist live-doc metadata + last snapshot across
    # container restarts (a named volume). Defaults to :memory: (tests / a doc
    # is ephemeral by design — the durable record is the vault snapshot).
    store = store or CoDocStore(os.environ.get("CODOC_DB", ":memory:"))
    app = FastAPI(title="emptyos-codoc")
    # doc_id -> {"peers": {websocket: level}, "doc": <pycrdt Doc>} for active rooms.
    rooms: dict[str, dict] = {}

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "emptyos-codoc"}

    @app.get("/editor/{doc_id}", response_class=HTMLResponse)
    async def editor(doc_id: str):
        # Static co-editor page (Yjs + CodeMirror over the WS protocol). The page
        # itself does no auth — the WS join is the gate; a non-member's socket is
        # closed and the page shows "not a member". doc_id is read client-side.
        return HTMLResponse((_WEB / "editor.html").read_text(encoding="utf-8"))

    @app.post("/api/docs")
    async def create_doc(request: Request):
        owner = _principal(request)
        body = await request.json() if await request.body() else {}
        title = (body or {}).get("title", "") if isinstance(body, dict) else ""
        return {"doc": store.create_doc(owner, str(title))}

    @app.get("/api/docs/{doc_id}")
    async def get_doc(request: Request, doc_id: str):
        p = _principal(request)
        doc = store.get_doc(doc_id)
        level = store.member_level(doc_id, p) if doc else None
        if doc is None or level is None:
            # Don't leak existence to a non-member.
            raise HTTPException(status_code=404, detail="doc not found")
        return {"doc": doc, "level": level}

    @app.post("/api/docs/{doc_id}/members")
    async def add_member(request: Request, doc_id: str):
        owner = _principal(request)
        doc = store.get_doc(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="doc not found")
        if doc["owner_id"] != owner:
            raise HTTPException(status_code=403, detail="only the owner may add members")
        body = await request.json()
        member = str((body or {}).get("principal", "")).strip()
        level = str((body or {}).get("level", "read")).strip()
        if not member:
            raise HTTPException(status_code=400, detail="principal is required")
        if level not in LEVELS:
            raise HTTPException(status_code=400, detail=f"level must be one of {LEVELS}")
        store.add_member(doc_id, member, level)
        return {"ok": True, "level": level}

    @app.post("/api/docs/{doc_id}/snapshot")
    async def snapshot(request: Request, doc_id: str):
        # Snapshot-back bridge: render the doc's current text (supplied by the
        # caller, who holds the merged Yjs text) as a vault-ready md note. The
        # service has no vault — the owner's daemon writes the returned markdown.
        p = _principal(request)
        doc = store.get_doc(doc_id)
        if doc is None or store.member_level(doc_id, p) is None:
            raise HTTPException(status_code=404, detail="doc not found")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be an object")
        text = str(body.get("text", ""))
        md = snapshot_markdown(doc, text, contributors=store.contributors(doc_id))
        return {"markdown": md, "filename": f"{slugify(doc['title'] or doc_id)}.md"}

    @app.websocket("/api/docs/{doc_id}/sync")
    async def sync(ws: WebSocket, doc_id: str):
        # ACL-gated join. Principal comes from the trusted control-plane hop's
        # X-EOS-Principal header; the ?principal= query is a DEV-ONLY fallback,
        # gated behind CODOC_DEV_WS_AUTH (off by default) — a raw client-supplied
        # principal is otherwise an identity-spoof. See _ws_principal.
        pid = _ws_principal(ws)
        level = store.member_level(doc_id, pid) if pid else None
        if level is None:
            await ws.close(code=1008)  # policy violation — not a member
            return
        await ws.accept()
        room = rooms.get(doc_id)
        if room is None:
            # First peer in — hydrate the server doc from persisted merged state.
            room = {"peers": {}, "doc": crdt.new_doc(store.get_state(doc_id))}
            rooms[doc_id] = room
        peers = room["peers"]
        peers[ws] = level
        # Bootstrap the joiner with the current merged Y.Doc state — but only
        # once the doc has real content (persisted state exists). A brand-new
        # empty doc needs no bootstrap (the joiner starts empty too).
        if store.get_state(doc_id):
            await ws.send_bytes(protocol.frame(protocol.DOC, crdt.full_state(room["doc"])))
        try:
            while True:
                raw = await ws.receive_bytes()
                kind, payload = protocol.parse(raw)
                if kind == protocol.AWARENESS:
                    # Presence / cursors — relay to peers (incl. from read-only
                    # observers), never applied to the doc.
                    for other in list(peers):
                        if other is not ws:
                            await other.send_bytes(raw)
                    continue
                # DOC update — write-gated, merged, persisted, relayed.
                if peers.get(ws) != "write":
                    continue  # read-only peers observe; they may not mutate
                if not crdt.apply_update(room["doc"], payload):
                    continue  # invalid update — never persist or propagate garbage
                store.save_state(doc_id, crdt.full_state(room["doc"]))
                for other in list(peers):
                    if other is not ws:
                        await other.send_bytes(raw)
        except WebSocketDisconnect:
            pass
        finally:
            peers.pop(ws, None)
            if not peers:
                # Last peer left — the merged state is already persisted on each
                # update; drop the in-memory room.
                rooms.pop(doc_id, None)

    app.state.store = store
    return app
