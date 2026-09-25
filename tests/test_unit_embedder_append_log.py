"""The embedding cache persists by appending records, never by rewriting it.

Until 2026-09-24 `Embedder.embed_many` saved by re-serialising the entire cache
after every call, synchronously, on the event loop. The main daemon's bge-m3
cache had reached 777 MB of JSON, roughly 10 s per rewrite (extrapolated from
a 124 MB sandbox cache, where json.dumps took 1.6 s). Aura embeds every new
sentence it hears, so each such reply waited on the rewrite, and the whole
daemon stalled with it.

Vectors now live in a binary append-only store (`<cache>.f32`). An existing
JSON cache is converted once, and the JSON files are left untouched.

Daemon-free: the embedding API is replaced with a fake client.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import textwrap
import threading

import pytest

import emptyos.sdk.embeddings as E
from emptyos.sdk.embeddings import STORE_MAGIC, Embedder, _decode_store, _sig


class _Item:
    def __init__(self, vec):
        self.embedding = vec


class _Resp:
    def __init__(self, vecs):
        self.data = [_Item(v) for v in vecs]


class _FakeClient:
    """Stands in for the OpenAI client. Each text embeds to [len(text), 1.0]."""

    def __init__(self, fail_times=0):
        self.calls = 0
        self.fail_times = fail_times
        self.embeddings = self

    def create(self, model, input):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("upstream down")
        return _Resp([[float(len(t)), 1.0] for t in input])


@pytest.fixture()
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def _make(client=None):
        e = Embedder(cache_path=tmp_path / "shared.json")
        e._client = client or _FakeClient()
        return e
    return _make


def _embed(e, texts):
    return asyncio.run(e.embed_many(texts))


def _record_count(path):
    """Records on disk, counting duplicates (a dict would hide them)."""
    data = path.read_bytes()
    assert data.startswith(STORE_MAGIC)
    pos, n = len(STORE_MAGIC), 0
    while pos < len(data):
        sig_len, count = E._REC_HEAD.unpack_from(data, pos)
        pos += E._REC_HEAD.size + sig_len + 4 * count
        n += 1
    assert pos == len(data), "store does not end on a record boundary"
    return n


def _write_legacy(e, base=None, log_lines=()):
    if base is not None:
        e.cache_path.write_text(json.dumps(base), encoding="utf-8")
    if log_lines:
        e.append_path.write_text(
            "".join(json.dumps(x) + "\n" for x in log_lines), encoding="utf-8")


# ── a fresh cache ──────────────────────────────────────────────────────


def test_a_fresh_cache_goes_straight_to_the_binary_store(make):
    e = make()
    assert _embed(e, ["hello"]) == [[5.0, 1.0]]
    assert e.store_path.exists()
    assert not e.cache_path.exists() and not e.append_path.exists()
    assert _decode_store(e.store_path.read_bytes())[0] == {_sig("hello"): [5.0, 1.0]}


def test_a_restart_reads_the_store(make):
    _embed(make(), ["one", "three"])
    client = _FakeClient()
    assert _embed(make(client), ["one", "three"]) == [[3.0, 1.0], [5.0, 1.0]]
    assert client.calls == 0, "a stored vector was embedded again after restart"


def test_a_cached_text_is_not_appended_again(make):
    e = make()
    _embed(e, ["same"])
    _embed(e, ["same"])
    assert _record_count(e.store_path) == 1


# ── converting an existing JSON cache ─────────────────────────────────


def test_the_json_cache_is_converted_once_and_left_untouched(make):
    e = make()
    _write_legacy(e, base={_sig("old"): [9.0, 9.0]},
                  log_lines=[[_sig("logged"), [6.0, 6.0]]])
    base_before = e.cache_path.read_bytes()
    log_before = e.append_path.read_bytes()

    assert _embed(e, ["old", "logged", "new"]) == [[9.0, 9.0], [6.0, 6.0], [3.0, 1.0]]

    assert e.cache_path.read_bytes() == base_before
    assert e.append_path.read_bytes() == log_before, "a new vector went to the JSON log"
    stored = _decode_store(e.store_path.read_bytes())[0]
    assert stored == {_sig("old"): [9.0, 9.0], _sig("logged"): [6.0, 6.0], _sig("new"): [3.0, 1.0]}

    client = _FakeClient()
    assert _embed(make(client), ["old", "logged", "new"]) == [[9.0, 9.0], [6.0, 6.0], [3.0, 1.0]]
    assert client.calls == 0


def test_once_the_store_exists_the_json_files_are_not_read(make):
    e = make()
    _write_legacy(e, base={_sig("old"): [9.0, 9.0]})
    _embed(e, ["old"])
    # A key the store does not hold: it would appear if the JSON were merged in.
    e.cache_path.write_text(json.dumps({_sig("only-in-json"): [1.0, 1.0]}), encoding="utf-8")
    assert make().cache == {_sig("old"): [9.0, 9.0]}


def test_a_failed_conversion_stays_on_json_and_loses_nothing(make, monkeypatch):
    e = make()
    _write_legacy(e, base={_sig("old"): [9.0, 9.0]})

    def broken(sig, vec):
        raise OSError("disk full")
    monkeypatch.setattr(E, "_encode_record", broken)
    _embed(e, ["new"])
    monkeypatch.undo()
    monkeypatch.setenv("EOS_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    assert not e.store_path.exists()
    assert not e.store_path.with_name(e.store_path.name + ".tmp").exists()
    # The new vector went to the JSON log, so the next load still has both.
    client = _FakeClient()
    assert _embed(make(client), ["old", "new"]) == [[9.0, 9.0], [3.0, 1.0]]
    assert client.calls == 0


def test_a_crash_mid_conversion_leaves_no_partial_store(make, monkeypatch):
    # A hard stop (process killed) skips every except/cleanup. If conversion
    # wrote the store in place, the partial file would be trusted on the next
    # load and the rest of the old cache would never be converted.
    e = make()
    _write_legacy(e, base={_sig("a"): [1.0, 1.0], _sig("b"): [2.0, 2.0]})
    calls = {"n": 0}
    real = E._encode_record

    def dies_on_second(sig, vec):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt  # not an Exception: no cleanup runs
        return real(sig, vec)
    monkeypatch.setattr(E, "_encode_record", dies_on_second)
    with pytest.raises(KeyboardInterrupt):
        _ = e.cache
    monkeypatch.setattr(E, "_encode_record", real)

    assert not e.store_path.exists(), "a partial store was left in place"
    client = _FakeClient()
    assert _embed(make(client), ["a", "b"]) == [[1.0, 1.0], [2.0, 2.0]]
    assert client.calls == 0


# ── damage ────────────────────────────────────────────────────────────


def test_a_torn_last_record_is_trimmed_and_the_next_append_is_kept(make):
    e = make()
    _embed(e, ["kept"])
    whole = e.store_path.read_bytes()
    with open(e.store_path, "ab") as f:
        f.write(E._encode_record("torn", [1.0] * 8)[:13])   # a crash mid-append

    again = make()
    assert _embed(again, ["kept"]) == [[4.0, 1.0]]
    assert again.store_path.read_bytes() == whole, "the torn record was not trimmed"

    _embed(again, ["after the tear"])
    client = _FakeClient()
    assert _embed(make(client), ["kept", "after the tear"]) == [[4.0, 1.0], [14.0, 1.0]]
    assert client.calls == 0, "the vector written after the torn record was lost"


def test_a_store_without_its_header_is_moved_aside_not_deleted(make):
    e = make()
    e.store_path.write_bytes(b"not a vector store")
    assert make().cache == {}
    aside = e.store_path.with_name(e.store_path.name + ".unreadable")
    assert aside.read_bytes() == b"not a vector store"


# ── failures, locking, the event loop ─────────────────────────────────


def test_a_failed_batch_is_cached_as_zeros_as_before(make):
    # Unchanged behaviour, pinned because the search indexer depends on it: it
    # records a note as indexed once its sig is in the cache. Not caching a
    # failure would drop the note from search instead of retrying it.
    client = _FakeClient(fail_times=1)
    e = make(client)

    assert _embed(e, ["failed"]) == [[0.0] * e.dim]
    assert _sig("failed") in e.cache
    assert _embed(e, ["failed"]) == [[0.0] * e.dim]
    assert client.calls == 1

    reloaded = make(_FakeClient())
    assert reloaded.cache.get(_sig("failed")) == [0.0] * e.dim


def test_two_embedders_on_one_file_do_not_interleave_records(make):
    # Real batch size: 64 bge-m3 vectors is ~260 KB per append, written in
    # chunks, so two unlocked writers can interleave. A barrier releases all
    # writers together and the race runs many rounds. It is still a race, so
    # treat a pass as strong evidence, not proof.
    a, b = make(), make()
    batches = [[(f"sig-{w}-{i}", [0.5 + i] * 1024) for i in range(64)] for w in range(8)]
    for _round in range(15):
        a.store_path.unlink(missing_ok=True)
        gate = threading.Barrier(len(batches))

        def write(embedder, batch):
            gate.wait()
            embedder._append(batch)

        threads = [threading.Thread(target=write, args=(a if w % 2 else b, batch))
                   for w, batch in enumerate(batches)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert _record_count(a.store_path) == 8 * 64
        stored = _decode_store(a.store_path.read_bytes())[0]
        assert len(stored) == 8 * 64 and all(k.startswith("sig-") for k in stored)


def test_the_append_runs_off_the_event_loop():
    # A synchronous write here is exactly the stall this change removes.
    tree = ast.parse(textwrap.dedent(inspect.getsource(Embedder.embed_many)))
    to_thread_targets = [
        ast.unparse(node.args[0]) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "asyncio.to_thread"
    ]
    assert "self._append" in to_thread_targets
    direct = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._append"
    ]
    assert not direct, "embed_many calls _append on the event loop"


@pytest.mark.parametrize("vec", [[0.0] * 1024, [0.1, -2.5, 3.25e-8], []])
def test_a_record_round_trips(vec):
    data = STORE_MAGIC + E._encode_record("s", vec)
    decoded, used, tail_ok = _decode_store(data)
    assert used == len(data) and tail_ok
    assert decoded == {"s": [float(x) for x in __import__("array").array("f", vec)]}


# ── review round 2: concurrency and damage ────────────────────────────


def _store_with(e, pairs):
    e.store_path.write_bytes(STORE_MAGIC + b"".join(E._encode_record(s, v) for s, v in pairs))


def test_two_first_loads_convert_once_and_lose_no_append(make):
    # Both instances load at once with a JSON cache present, then append.
    # Without the load lock both converted, and the later os.replace put its
    # snapshot over the store, dropping the other's append.
    for _round in range(10):
        a, b = make(), make()
        for p in (a.store_path, a.cache_path, a.append_path):
            p.unlink(missing_ok=True)
        _write_legacy(a, base={_sig("old"): [9.0, 9.0]})
        gate = threading.Barrier(2)

        def go(embedder, sig):
            gate.wait()
            _ = embedder.cache
            embedder._append([(sig, [1.0, 2.0])])

        threads = [threading.Thread(target=go, args=(a, "from-a")),
                   threading.Thread(target=go, args=(b, "from-b"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert make().cache == {_sig("old"): [9.0, 9.0], "from-a": [1.0, 2.0], "from-b": [1.0, 2.0]}


def test_a_trim_never_cuts_an_append_that_raced_it(make):
    for _round in range(10):
        e = make()
        _store_with(e, [("kept", [1.0] * 256)])
        with open(e.store_path, "ab") as f:
            f.write(E._encode_record("torn", [1.0] * 256)[:20])
        loader, writer = make(), make()
        batch = [(f"new-{i}", [2.0] * 256) for i in range(64)]
        gate = threading.Barrier(2)

        def load():
            gate.wait()
            _ = loader.cache

        def write():
            gate.wait()
            writer._append(batch)

        threads = [threading.Thread(target=load), threading.Thread(target=write)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stored, used, tail_ok = _decode_store(e.store_path.read_bytes())
        assert used == e.store_path.stat().st_size and tail_ok
        assert set(stored) == {"kept"} | {s for s, _ in batch}


def test_damage_mid_file_is_moved_aside_not_trimmed(make):
    e = make()
    _store_with(e, [("a", [1.0] * 8), ("b", [2.0] * 8), ("c", [3.0] * 8)])
    data = bytearray(e.store_path.read_bytes())
    # Record "b" claims a far longer vector than the bytes that follow hold.
    b_at = len(STORE_MAGIC) + len(E._encode_record("a", [1.0] * 8))
    E._REC_HEAD.pack_into(data, b_at, 1, 5000)
    e.store_path.write_bytes(bytes(data))

    assert make().cache == {}
    aside = e.store_path.with_name(e.store_path.name + ".unreadable")
    assert aside.read_bytes() == bytes(data), "good records after the damage were not kept"


def test_an_impossible_vector_length_is_damage_not_a_torn_tail(make):
    e = make()
    _store_with(e, [("a", [1.0] * 8)])
    with open(e.store_path, "ab") as f:
        f.write(E._REC_HEAD.pack(1, E._MAX_DIM + 1) + b"x")
    assert make().cache == {}
    assert e.store_path.with_name(e.store_path.name + ".unreadable").exists()


def test_a_second_damaged_store_does_not_overwrite_the_first_aside(make):
    e = make()
    e.store_path.write_bytes(b"first bad store")
    _ = make().cache
    e.store_path.write_bytes(b"second bad store")
    _ = make().cache
    first = e.store_path.with_name(e.store_path.name + ".unreadable")
    second = e.store_path.with_name(e.store_path.name + ".unreadable.1")
    assert first.read_bytes() == b"first bad store"
    assert second.read_bytes() == b"second bad store"


def test_a_vector_reads_the_same_before_and_after_a_restart(make, monkeypatch):
    class _Precise(_FakeClient):
        def create(self, model, input):
            self.calls += 1
            return _Resp([[0.1234567890123, 1.0] for _ in input])
    e = make(_Precise())
    before = _embed(e, ["x"])
    assert _embed(make(_FakeClient()), ["x"]) == before
