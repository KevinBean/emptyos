from __future__ import annotations

import asyncio
import base64
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from emptyos.web.routes_vault import register_vault_file_routes


class _Events:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict, str]] = []

    async def emit(self, event: str, payload: dict, *, source: str) -> None:
        self.rows.append((event, payload, source))


class _VaultMap:
    def all(self):
        return {}

    def set(self, *_args):
        return None

    def rescan(self):
        return []


def _client(vault):
    events = _Events()
    kernel = SimpleNamespace(
        config=SimpleNamespace(notes_path=vault),
        events=events,
        vault_map=_VaultMap(),
        services=SimpleNamespace(get_optional=lambda _name: None),
    )
    app = FastAPI()
    register_vault_file_routes(app, kernel)
    return TestClient(app), events


def _payload(path: str, content: bytes, **extra):
    return {
        "path": path,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        **extra,
    }


def test_binary_write_hashes_reads_back_and_reuses(tmp_path):
    client, events = _client(tmp_path)
    content = b"\x89PNG\r\n\x1a\noriginal-evidence"
    payload = _payload("40_Archive/AI Conversations/assets/evidence.png", content)

    written = client.post("/api/vault/write-bytes", json=payload)
    assert written.status_code == 200
    assert written.json()["status"] == "written"
    assert written.json()["content_sha256"] == payload["content_sha256"]
    assert (tmp_path / payload["path"]).read_bytes() == content

    reused = client.post("/api/vault/write-bytes", json=payload)
    assert reused.status_code == 200
    assert reused.json()["status"] == "reused"
    assert len(events.rows) == 1

    served = client.get("/api/vault/file", params={"path": payload["path"]})
    assert served.status_code == 200
    assert served.content == content
    assert served.headers["content-type"].startswith("image/png")
    assert "content-disposition" not in served.headers


def test_svg_is_served_as_attachment_png_is_not(tmp_path):
    """SVG can carry a <script> that runs if opened as a top-level
    navigation (e.g. window.open on a vault attachment chip). Forcing
    Content-Disposition: attachment on .svg neutralizes that same-origin
    XSS without touching <img src> embeds, which ignore the header."""
    client, _events = _client(tmp_path)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    png = b"\x89PNG\r\n\x1a\nnot-a-real-png"
    client.post("/api/vault/write-bytes", json=_payload("assets/x.svg", svg))
    client.post("/api/vault/write-bytes", json=_payload("assets/x.png", png))

    svg_resp = client.get("/api/vault/file", params={"path": "assets/x.svg"})
    assert svg_resp.status_code == 200
    assert svg_resp.headers["content-type"].startswith("image/svg+xml")
    assert svg_resp.headers["content-disposition"] == "attachment"

    png_resp = client.get("/api/vault/file", params={"path": "assets/x.png"})
    assert png_resp.status_code == 200
    assert "content-disposition" not in png_resp.headers


def test_binary_write_rejects_conflict_without_explicit_overwrite(tmp_path):
    client, _events = _client(tmp_path)
    path = "40_Archive/AI Conversations/assets/evidence.bin"
    assert client.post(
        "/api/vault/write-bytes", json=_payload(path, b"first")
    ).status_code == 200

    conflict = client.post(
        "/api/vault/write-bytes", json=_payload(path, b"second")
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "Immutable binary conflict"
    assert (tmp_path / path).read_bytes() == b"first"

    overwritten = client.post(
        "/api/vault/write-bytes",
        json=_payload(path, b"second", overwrite=True),
    )
    assert overwritten.status_code == 200
    assert overwritten.json()["status"] == "overwritten"
    assert (tmp_path / path).read_bytes() == b"second"


def test_binary_write_rejects_bad_hash_base64_and_traversal(tmp_path):
    client, _events = _client(tmp_path)

    bad_hash = _payload("asset.bin", b"content")
    bad_hash["content_sha256"] = "0" * 64
    assert client.post(
        "/api/vault/write-bytes", json=bad_hash
    ).status_code == 400

    bad_base64 = _payload("asset.bin", b"content")
    bad_base64["content_base64"] = "***"
    assert client.post(
        "/api/vault/write-bytes", json=bad_base64
    ).status_code == 400

    traversal = _payload("../../outside.bin", b"content")
    assert client.post(
        "/api/vault/write-bytes", json=traversal
    ).status_code == 403


class _Request:
    """Minimal stand-in — the route reads `headers` then `await json()`."""

    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    async def json(self) -> dict:
        return self._payload


def _endpoint(app: FastAPI, path: str):
    return next(r.endpoint for r in app.routes if getattr(r, "path", "") == path)


@pytest.mark.asyncio
async def test_binary_write_yields_the_event_loop(tmp_path):
    """The write path fsyncs, and an fsync on the loop thread freezes the daemon.

    TestClient is synchronous, so the other tests in this file cannot see this:
    they pass identically whether the filesystem work runs inline or in a
    thread. Here the route is awaited directly with a ticker racing it. If any
    of the read/write/fsync/readback runs inline, the coroutine never suspends,
    the ticker never gets a turn, and `ticks` stays 0 — which is exactly what
    the whole event bus, every other request, and every scheduled job would be
    doing for the duration of the fsync.
    """
    app = FastAPI()
    register_vault_file_routes(app, SimpleNamespace(
        config=SimpleNamespace(notes_path=tmp_path),
        events=_Events(),
        vault_map=_VaultMap(),
        services=SimpleNamespace(get_optional=lambda _name: None),
    ))

    content = b"\x89PNG\r\n\x1a\n" + b"payload" * 4096
    request = _Request(_payload("40_Archive/assets/big.png", content))

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    spinner = asyncio.create_task(ticker())
    try:
        result = await _endpoint(app, "/api/vault/write-bytes")(request)
    finally:
        spinner.cancel()

    assert result["ok"] is True and result["status"] == "written"
    assert ticks > 0, (
        "the write ran to completion without suspending — the filesystem work "
        "is back on the event loop thread (wrap it in asyncio.to_thread)"
    )


@pytest.mark.asyncio
async def test_oversized_body_is_refused_on_the_header_not_after_buffering(tmp_path):
    """413 must land before `await request.json()` reads the whole body.

    Checking len(content) afterwards is a correct answer paid for at peak
    memory — raw body + parsed str + decoded bytes — and a body that lies about
    its length is unbounded either way. The header check is what makes the cap
    a cap rather than a report.
    """
    app = FastAPI()
    register_vault_file_routes(app, SimpleNamespace(
        config=SimpleNamespace(notes_path=tmp_path),
        events=_Events(),
        vault_map=_VaultMap(),
        services=SimpleNamespace(get_optional=lambda _name: None),
    ))

    read = False

    class _ExplodingRequest(_Request):
        async def json(self):
            nonlocal read
            read = True
            return self._payload

    request = _ExplodingRequest(
        _payload("asset.bin", b"tiny"),
        headers={"content-length": str(500 * 1024 * 1024)},
    )
    response = await _endpoint(app, "/api/vault/write-bytes")(request)

    assert response.status_code == 413
    assert read is False, "the body was buffered before the size was refused"


@pytest.mark.asyncio
async def test_concurrent_writes_of_different_bytes_cannot_both_win(tmp_path):
    """Immutable-by-default must be an invariant, not a check that usually holds.

    exists() and os.replace are separated by a mkdir, a write and an fsync. Two
    concurrent posts of different bytes both saw "not there", both wrote, and
    os.replace silently last-won: no 409, no corruption, and no contract either.
    Serializing on the note is what turns the check into a guarantee.

    Asserted as "exactly one 200 and one 409" rather than naming a winner —
    which request arrives first is legitimately a race; both being told they
    won is the bug.
    """
    app = FastAPI()
    register_vault_file_routes(app, SimpleNamespace(
        config=SimpleNamespace(notes_path=tmp_path),
        events=_Events(),
        vault_map=_VaultMap(),
        services=SimpleNamespace(get_optional=lambda _name: None),
    ))
    endpoint = _endpoint(app, "/api/vault/write-bytes")
    path = "40_Archive/assets/contested.bin"

    results = await asyncio.gather(
        endpoint(_Request(_payload(path, b"first writer"))),
        endpoint(_Request(_payload(path, b"second writer"))),
    )
    codes = sorted(getattr(r, "status_code", 200) for r in results)

    assert codes == [200, 409], f"both writers were told they succeeded: {codes}"
    written = (tmp_path / path).read_bytes()
    assert written in (b"first writer", b"second writer")
    winner = next(r for r in results if getattr(r, "status_code", 200) == 200)
    assert winner["content_sha256"] == hashlib.sha256(written).hexdigest(), (
        "the reported hash is not what is actually on disk"
    )


def test_route_and_app_land_on_the_same_lock_object():
    """Same note, two writers, one lock — asserted through the real registry.

    The first version of this test re-implemented both normalisations inline and
    compared the strings. That is the weak shape: it would have stayed green
    while BaseApp.note_lock changed its rule and production drifted back onto
    two private locks. So drive the real VaultIndex.note_lock from both sides
    and assert object identity — the thing that actually decides whether one
    writer excludes the other.
    """
    import os
    from pathlib import Path as _P

    from emptyos.runtime.vault_index import VaultIndex
    from emptyos.web.routes_vault import _note_lock

    index = VaultIndex.__new__(VaultIndex)   # registry only; no vault scan
    index._note_locks = {}
    kernel = SimpleNamespace(services=SimpleNamespace(
        get_optional=lambda name: index if name == "vault_index" else None))

    # Any absolute, space-bearing root works — the assertion is about the key
    # both sides derive, not about this machine. Rule 13 keeps real vault paths
    # out of tracked files, and check-personal gates on exactly this pattern.
    vault_root = _P("X:/Example Vault") if os.name == "nt" else _P("/vault")
    rel = "40_Archive/AI Conversations/assets/evidence.png"

    # What an app hands VaultIndex (BaseApp.note_lock, relative-path branch).
    app_lock = index.note_lock(rel)

    # What the route hands it, derived from the absolute candidate.
    candidate = vault_root / rel
    route_key = os.path.relpath(os.path.normpath(str(candidate)), vault_root)
    route_lock = _note_lock(kernel, route_key)

    assert route_lock is app_lock, (
        "the route and an app took different lock objects for one note — "
        "neither excludes the other"
    )
