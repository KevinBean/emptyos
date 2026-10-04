# emptyos-codoc — live collaboration service (Lane 1)

The **live** real-time collaboration layer the single-user daemon never had
(Phase 2 of the collaboration roadmap). A Lane-1 HTTP+WebSocket service with **no
vault** and **never inside the daemon** — multiple principals open the same
document at once, edits relayed in real time, gated by who may join and at what
level.

It sits in the three-surface model: **live doc = ephemeral working surface**;
the **vault = durable** per-user record; the **commons = published**. A snapshot
bridges live → vault (Phase 4).

## Trust model (reused from Phase 1)

The acting principal arrives in the **`X-EOS-Principal`** header, set by the
trusted control-plane hop — exactly like `X-EOS-Actor` on the daemon's delegation
endpoint. A browser can't forge it (the proxy composes it server-side and never
forwards browser copies); the service only ever trusts the hop. In local/dev the
header is supplied directly.

## API

| Route | Auth | What |
|---|---|---|
| `POST /api/docs` | principal | create a doc (caller = owner) |
| `GET /api/docs/{id}` | member | metadata + the caller's level (404 to non-members — no existence leak) |
| `POST /api/docs/{id}/members` | owner | grant a principal `read`/`write` on the doc (ACL-gated join) |
| `WS /api/docs/{id}/sync` | member | join the live session — hydrated with the last snapshot; `write` members' frames are relayed to peers + persisted; `read` members observe only |
| `POST /api/docs/{id}/snapshot` | member | snapshot-back bridge — render the doc's current text (caller-supplied) as a vault-ready `author: both` md note; the owner's daemon writes it |

Reversibility line holds: CRDT char-edits are reversible (history = audit/undo)
so they flow freely; an **irreversible** action triggered from the live doc
(publish/send/deploy) routes back through the **daemon's `[DO:]` gate** — it is
not performed by this service.

## Scope (honest — this is the foundation, not the whole feature)

**Built + tested here (21 tests):** ACL-gated join, the real-time relay + presence
fan-out (under uvicorn), **server-side CRDT merge** — a `pycrdt` `Y.Doc` per live
room that merges each client's Yjs update (commutative), persists the merged full
state, and bootstraps late joiners with it (garbage updates rejected, corrupt
snapshots start empty — never brick a room), the **agent live-co-editor client**
(`CoDocClient` — the Phase-3 mechanism: an agent joins + edits with the same CRDT
machinery a human's browser uses, attributed by principal; converges with a human
in tests), the membership/level store, and the **snapshot-back bridge** (Phase 4)
— render a doc's current text as an `author: both` vault note (the owner's daemon
writes it; Lane 1 has no vault).

**Browser editor — BROWSER-VERIFIED working (2026-06-27).** `GET /editor/<doc-id>?as=<principal>`
serves `emptyos_codoc/web/editor.html`: the canonical Yjs + CodeMirror 6
(`y-codemirror.next`) stack with a ~20-line custom WS provider speaking this
service's exact protocol (server sends merged full state on join + relays raw
updates; client applies inbound + sends local updates). Verified end-to-end with
two real browser tabs (Playwright): alice (owner) and bob (write member) edit the
same doc and changes sync **both directions** — and **live presence + cursors**
work (each tab shows "2 editing: alice, bob" and renders the other's named caret)
— all through the ACL-gated WS + the pycrdt server merge. (The browser test
caught a real CM6 import bug `node --check` couldn't — `EditorView`/`basicSetup`
live in different packages.)

The WS carries two message kinds on one socket via a 1-byte type tag
(`emptyos_codoc/protocol.py`): `DOC` (Yjs updates — merged + persisted + relayed)
and `AWARENESS` (presence/cursors — relayed only, never applied to the doc). The
editor uses CodeMirror's markdown language mode (syntax highlighting), verified
in-browser.

**Next slices (daemon-side integration):**
- **Agent runtime wiring** — spawn `CoDocClient` from the daemon's agent runtime,
  connect the room WS as `agent:<id>`, drive `bootstrap`/`insert`/`receive`. The
  *mechanism* (`client.py`) + the server merge + the agent registry/invocation all
  exist; this is the cross-component glue (needs the running daemon + the service).
- **Snapshot write-side** — the daemon endpoint that takes this service's
  markdown and writes it to the vault with `BaseApp.vault_create_note`.

**Deploy** (Lane-1, behind the control-plane proxy):
```
cp .env.example .env        # optional — sane defaults
bash scripts/deploy-service.sh emptyos-codoc   # → loopback :9400, healthcheck /health
```
Then add codoc's loopback address to the control-plane routing table (it injects
`X-EOS-Principal`). The container is hardened (read-only rootfs + dropped caps);
live-doc state persists in the `codoc-data` volume via `CODOC_DB`. The artifacts
(`Dockerfile`, `docker-compose.yml`, `service.toml`, `.env.example`) are built +
validated; a real `docker compose build` on a host is the only unrun step.

## Run

```
pip install -e ".[test]"
python -m pytest tests/ -q
uvicorn emptyos_codoc.app:create_app --factory --port 8700
```
