"""Co-doc service — ACL-gated join + persistence + membership.

Proves the Phase-2 foundation: who may open a live doc (and at what level), that
a joiner is hydrated with the persisted snapshot, and the membership/persistence
store logic. The CRDT frames are opaque bytes (merge logic is the browser's Yjs
client / the Phase-3 server-side y-py).

Note: the live peer-to-peer broadcast (one writer's frame fanned out to other
*currently-connected* sockets) runs under real uvicorn but is not exercised here
— Starlette's TestClient drives the ASGI app through a single portal, so two
simultaneous test websockets deadlock. The receive→persist→hydrate path (tested
below) and the broadcast share the same handler; the broadcast is integration-
tested at deploy time.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pycrdt import Doc, Text
from starlette.websockets import WebSocketDisconnect

from emptyos_codoc import CoDocClient, CoDocStore, create_app
from emptyos_codoc.protocol import AWARENESS, DOC
from emptyos_codoc.protocol import frame as _pframe
from emptyos_codoc.protocol import parse as _parse
from emptyos_codoc.snapshot import slugify, snapshot_markdown


def _frame(text, pos=0):
    """A real Yjs update inserting `text` at `pos` into shared text 't'."""
    d = Doc()
    d["t"] = Text()
    d["t"].insert(pos, text)
    return d.get_update()


def _read(update):
    """Decode the text from a Y.Doc update (shared text 't')."""
    d = Doc()
    t = Text()
    d["t"] = t
    d.apply_update(update)
    return str(t)


def _client():
    return TestClient(create_app(CoDocStore(":memory:")))


def _h(principal):
    return {"X-EOS-Principal": principal}


def _new_doc(c, owner="alice", title=""):
    return c.post("/api/docs", headers=_h(owner), json={"title": title}).json()["doc"]


# ---- REST + ACL ---------------------------------------------------------- #

def test_owner_creates_and_has_write():
    c = _client()
    doc = _new_doc(c, "alice", "Plan")
    assert doc["owner_id"] == "alice" and doc["title"] == "Plan"
    r = c.get(f"/api/docs/{doc['id']}", headers=_h("alice"))
    assert r.status_code == 200 and r.json()["level"] == "write"


def test_non_member_cannot_discover_doc():
    c = _client()
    doc = _new_doc(c)
    assert c.get(f"/api/docs/{doc['id']}", headers=_h("bob")).status_code == 404


def test_owner_adds_member_who_can_then_read():
    c = _client()
    doc = _new_doc(c)
    r = c.post(
        f"/api/docs/{doc['id']}/members",
        headers=_h("alice"), json={"principal": "bob", "level": "read"},
    )
    assert r.status_code == 200 and r.json()["level"] == "read"
    assert c.get(f"/api/docs/{doc['id']}", headers=_h("bob")).json()["level"] == "read"


def test_only_owner_adds_members():
    c = _client()
    doc = _new_doc(c)
    r = c.post(
        f"/api/docs/{doc['id']}/members",
        headers=_h("bob"), json={"principal": "carol"},
    )
    assert r.status_code == 403


def test_bad_level_rejected():
    c = _client()
    doc = _new_doc(c)
    r = c.post(
        f"/api/docs/{doc['id']}/members",
        headers=_h("alice"), json={"principal": "bob", "level": "admin"},
    )
    assert r.status_code == 400


def test_unauthenticated_rejected():
    c = _client()
    assert c.post("/api/docs", json={}).status_code == 401


def test_create_app_honors_codoc_db_env_for_persistence(tmp_path, monkeypatch):
    # The deploy persists live-doc state to a file via CODOC_DB (volume); a fresh
    # app on the same file sees prior docs (survives a container restart).
    monkeypatch.setenv("CODOC_DB", str(tmp_path / "codoc.db"))
    doc = TestClient(create_app()).post(
        "/api/docs", headers=_h("alice"), json={"title": "persist"},
    ).json()["doc"]
    r = TestClient(create_app()).get(f"/api/docs/{doc['id']}", headers=_h("alice"))
    assert r.status_code == 200 and r.json()["doc"]["title"] == "persist"


# ---- WebSocket join (single-portal, deterministic) ----------------------- #

def test_ws_rejects_non_member_join():
    c = _client()
    doc = _new_doc(c)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(
            f"/api/docs/{doc['id']}/sync", headers=_h("stranger")
        ) as ws:
            ws.receive_bytes()


def test_editor_page_is_served():
    c = _client()
    r = c.get("/editor/some-doc-id")
    assert r.status_code == 200
    body = r.text.lower()
    assert "yjs" in body and "codemirror" in body      # the editor stack
    assert "/api/docs/" in r.text and "principal=" in r.text  # wires the WS join


def test_ws_principal_via_query_fallback(monkeypatch):
    # Dev-only fallback: a browser WebSocket can't set headers, so ?principal=
    # is honoured ONLY when CODOC_DEV_WS_AUTH is set.
    monkeypatch.setenv("CODOC_DEV_WS_AUTH", "1")
    store = CoDocStore(":memory:")
    c = TestClient(create_app(store))
    doc = _new_doc(c, "alice")
    store.save_state(doc["id"], _frame("hello via query"))
    with c.websocket_connect(f"/api/docs/{doc['id']}/sync?principal=alice") as ws:
        kind, payload = _parse(ws.receive_bytes())
        assert kind == 0 and _read(payload) == "hello via query"  # DOC-framed bootstrap
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(
            f"/api/docs/{doc['id']}/sync?principal=stranger"
        ) as ws:
            ws.receive_bytes()


def test_ws_query_principal_rejected_without_dev_flag(monkeypatch):
    # Security: with the dev flag OFF (the production default), a client-supplied
    # ?principal= is an identity-spoof and must be ignored — even for a real
    # member. Only the trusted-hop X-EOS-Principal header is accepted.
    monkeypatch.delenv("CODOC_DEV_WS_AUTH", raising=False)
    store = CoDocStore(":memory:")
    c = TestClient(create_app(store))
    doc = _new_doc(c, "alice")  # alice is the owner — a real member
    store.save_state(doc["id"], _frame("secret body"))
    # ?principal=alice must NOT authenticate without the dev flag → socket closed.
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/api/docs/{doc['id']}/sync?principal=alice") as ws:
            ws.receive_bytes()
    # The trusted-hop header still authenticates the same member.
    with c.websocket_connect(f"/api/docs/{doc['id']}/sync", headers=_h("alice")) as ws:
        kind, payload = _parse(ws.receive_bytes())
        assert kind == 0 and _read(payload) == "secret body"


def test_ws_member_join_is_hydrated_with_merged_state():
    store = CoDocStore(":memory:")
    c = TestClient(create_app(store))
    doc = _new_doc(c)
    store.save_state(doc["id"], _frame("seeded body"))  # a prior session's merged state
    c.post(f"/api/docs/{doc['id']}/members", headers=_h("alice"),
           json={"principal": "bob", "level": "read"})
    with c.websocket_connect(f"/api/docs/{doc['id']}/sync", headers=_h("bob")) as ws:
        kind, payload = _parse(ws.receive_bytes())
        assert kind == 0 and _read(payload) == "seeded body"  # DOC-framed bootstrap


# ---- Wire protocol (DOC vs AWARENESS framing) ---------------------------- #

def test_protocol_frame_parse_roundtrip():
    assert _parse(_pframe(DOC, b"doc-bytes")) == (DOC, b"doc-bytes")
    assert _parse(_pframe(AWARENESS, b"presence")) == (AWARENESS, b"presence")
    assert _parse(b"") == (DOC, b"")          # empty frame is a harmless DOC no-op
    assert DOC != AWARENESS


# ---- Server-side CRDT merge (the Phase-3 foundation) --------------------- #
# Tested at the crdt/store layer — deterministic, no websockets. (The live
# peer fan-out is integration-tested under uvicorn; nested TestClient websockets
# deadlock on a single portal, so the merge correctness is proven here instead.)

def test_crdt_server_merge_converges():
    from emptyos_codoc import crdt
    doc = crdt.new_doc(None)
    assert crdt.apply_update(doc, _frame("alice "))   # two independent edits…
    assert crdt.apply_update(doc, _frame("bob "))      # …merge commutatively
    merged = _read(crdt.full_state(doc))
    assert "alice " in merged and "bob " in merged


def test_crdt_new_doc_hydrates_from_persisted_state():
    from emptyos_codoc import crdt
    doc = crdt.new_doc(_frame("seeded body"))
    assert _read(crdt.full_state(doc)) == "seeded body"


def test_crdt_rejects_garbage_without_corrupting_doc():
    from emptyos_codoc import crdt
    doc = crdt.new_doc(None)
    assert crdt.apply_update(doc, b"not-a-valid-yjs-update") is False
    assert crdt.apply_update(doc, _frame("ok")) is True   # doc still usable
    assert _read(crdt.full_state(doc)) == "ok"


def test_crdt_corrupt_persisted_state_starts_empty_not_raises():
    from emptyos_codoc import crdt
    doc = crdt.new_doc(b"garbage-snapshot")   # must not raise
    assert crdt.apply_update(doc, _frame("fresh")) is True
    assert _read(crdt.full_state(doc)) == "fresh"


# ---- Agent live co-editor (Phase 3 mechanism) ---------------------------- #

def test_agent_client_co_edits_with_a_human_and_converges():
    # A "human" seeds the doc; its full state is what the server would bootstrap.
    human = CoDocClient("human:alice")
    human.insert(0, "human draft. ")
    # The agent joins, bootstrapped with the shared state (so its diffs anchor).
    agent = CoDocClient("agent:finance-bot")
    agent.bootstrap(human.state)
    assert agent.text == "human draft. "
    # The agent contributes — programmatically, attributed to its principal.
    agent_diff = agent.append("agent note. ")
    # The human merges the agent's diff; concurrently the human keeps typing.
    human_diff = human.append("more human. ")
    assert human.receive(agent_diff)
    assert agent.receive(human_diff)
    # Both converge to the same merged text holding all three edits.
    assert human.text == agent.text
    for fragment in ("human draft.", "agent note.", "more human."):
        assert fragment in human.text


def test_agent_client_ignores_garbage_inbound():
    agent = CoDocClient("agent:x")
    agent.bootstrap(None)
    assert agent.receive(b"not-a-yjs-update") is False
    assert agent.append("ok") and agent.text == "ok"


# ---- Store logic (the relay's membership + persistence primitives) ------- #

def test_store_member_level_owner_member_stranger():
    s = CoDocStore(":memory:")
    doc = s.create_doc("alice")
    assert s.member_level(doc["id"], "alice") == "write"   # owner is implicit write
    assert s.member_level(doc["id"], "bob") is None        # not a member
    s.add_member(doc["id"], "bob", "read")
    assert s.member_level(doc["id"], "bob") == "read"
    s.add_member(doc["id"], "bob", "write")                # upsert upgrades
    assert s.member_level(doc["id"], "bob") == "write"
    assert s.member_level("no-such-doc", "alice") is None


def test_store_state_roundtrip():
    s = CoDocStore(":memory:")
    doc = s.create_doc("alice")
    assert s.get_state(doc["id"]) is None
    s.save_state(doc["id"], b"frame-a")
    assert s.get_state(doc["id"]) == b"frame-a"
    s.save_state(doc["id"], b"frame-b")                    # last-write snapshot
    assert s.get_state(doc["id"]) == b"frame-b"


def test_store_contributors_are_owner_plus_write_members():
    s = CoDocStore(":memory:")
    doc = s.create_doc("alice")
    s.add_member(doc["id"], "bob", "write")
    s.add_member(doc["id"], "carol", "read")  # readers didn't author
    assert s.contributors(doc["id"]) == ["alice", "bob"]


# ---- Snapshot-back (Phase 4 bridge) -------------------------------------- #

def test_snapshot_markdown_marks_co_authorship():
    doc = {"id": "d1", "owner_id": "alice", "title": "Q3 Plan",
           "created": "2026-06-27T00:00:00Z", "updated": "2026-06-27T01:00:00Z"}
    md = snapshot_markdown(doc, "  Hello world  ", contributors=["alice", "bob"])
    assert md.startswith("---\n")
    assert "author: both" in md          # never silently single-authored
    assert "source: codoc" in md
    assert "codoc_id: d1" in md
    assert "title: Q3 Plan" in md
    assert "  - alice" in md and "  - bob" in md  # block-style list (vault convention)
    assert md.rstrip().endswith("Hello world")    # trimmed body after frontmatter


def test_snapshot_endpoint_returns_vault_note_for_members():
    c = _client()
    doc = _new_doc(c, "alice", "Shared Draft")
    c.post(f"/api/docs/{doc['id']}/members", headers=_h("alice"),
           json={"principal": "bob", "level": "write"})
    r = c.post(f"/api/docs/{doc['id']}/snapshot", headers=_h("bob"),
               json={"text": "the merged body"})
    assert r.status_code == 200
    out = r.json()
    assert "author: both" in out["markdown"] and "the merged body" in out["markdown"]
    assert out["filename"] == "shared-draft.md"


def test_snapshot_rejected_for_non_member():
    c = _client()
    doc = _new_doc(c)
    r = c.post(f"/api/docs/{doc['id']}/snapshot", headers=_h("stranger"),
               json={"text": "x"})
    assert r.status_code == 404


def test_slugify():
    assert slugify("Q3 Plan!! ") == "q3-plan"
    assert slugify("") == "co-doc"
