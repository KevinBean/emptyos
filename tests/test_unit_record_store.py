"""JsonRecordStore — the guarded id-keyed JSON store.

Both defects it exists to fix are pinned here in both directions: a traversal
id must be refused, and a legitimate id must still round-trip.
"""

from __future__ import annotations

import json

import pytest

from emptyos.sdk import JsonRecordStore


def _store(tmp_path, **kw) -> JsonRecordStore:
    return JsonRecordStore(tmp_path / "queue", **kw)


# ── path safety ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../escape",
    "..\\escape",          # the one that actually reached through the route
    "sub/nested",
    "sub\\nested",
    "",
    "..",
    "nul",                 # Windows device stem: a write silently discards
])
def test_path_refuses_ids_that_escape_or_are_reserved(tmp_path, bad):
    store = _store(tmp_path, label="action id")
    with pytest.raises(ValueError):
        store.path(bad)
    # The read side soft-misses instead of raising — callers' contract is
    # `dict | None`, and a malformed id is a lookup that found nothing.
    assert store.load(bad) is None
    assert store.exists(bad) is False
    assert store.delete(bad) is False


def test_traversal_id_cannot_read_or_clobber_a_neighbouring_file(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "CANARY.json"
    outside.write_text(json.dumps({"id": "CANARY", "secret": True}), encoding="utf-8")

    assert store.load("../CANARY") is None
    assert store.load(r"..\CANARY") is None
    with pytest.raises(ValueError):
        store.save({"id": r"..\CANARY", "clobbered": True})

    # Untouched: same bytes, still parseable, no `clobbered` key.
    assert json.loads(outside.read_text(encoding="utf-8")) == {"id": "CANARY", "secret": True}


def test_real_ids_from_every_live_store_round_trip(tmp_path):
    store = _store(tmp_path)
    for rid in ["act-abc1234567", "camp-a1b2c3d4", "rr-case-grep-20260808T000000",
                "linkedin-20260802T124540682155Z", "seed-cyclic-rating-factor"]:
        store.save({"id": rid, "status": "pending"})
        assert store.load(rid) == {"id": rid, "status": "pending"}


# ── crash safety + tolerance ────────────────────────────────────────

def test_save_leaves_no_temp_debris_and_is_readable(tmp_path):
    store = _store(tmp_path)
    store.save({"id": "act-1", "body": "ünïcode stays raw"})
    files = sorted(p.name for p in (tmp_path / "queue").iterdir())
    assert files == ["act-1.json"]              # no *.tmp survivor
    raw = (tmp_path / "queue" / "act-1.json").read_text(encoding="utf-8")
    assert "ünïcode stays raw" in raw           # ensure_ascii=False


def test_a_torn_record_is_skipped_not_fatal(tmp_path):
    store = _store(tmp_path)
    store.save({"id": "act-good", "status": "pending"})
    (tmp_path / "queue" / "act-torn.json").write_text('{"id": "act-torn", "st', encoding="utf-8")
    (tmp_path / "queue" / "act-list.json").write_text("[1, 2, 3]", encoding="utf-8")

    # One corrupt entry must not blank the review queue.
    assert [r["id"] for r in store.list()] == ["act-good"]
    assert store.load("act-torn") is None
    assert store.load("act-list") is None       # valid JSON, wrong shape


def test_pattern_narrows_listing_and_missing_dir_lists_empty(tmp_path):
    store = _store(tmp_path, pattern="camp-*.json")
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "camp-1.json").write_text('{"id":"camp-1"}', encoding="utf-8")
    (tmp_path / "queue" / "other-1.json").write_text('{"id":"other-1"}', encoding="utf-8")
    assert [r["id"] for r in store.list()] == ["camp-1"]

    assert JsonRecordStore(tmp_path / "never-created").list() == []


def test_delete_removes_only_the_named_record(tmp_path):
    store = _store(tmp_path)
    store.save({"id": "act-1"})
    store.save({"id": "act-2"})
    assert store.delete("act-1") is True
    assert store.delete("act-1") is False       # already gone
    assert [r["id"] for r in store.list()] == ["act-2"]
