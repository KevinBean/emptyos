"""Pure tests for `aggregate_items` (conversation-ingest-single-import-scope).

`list_items` requires one import key at a time; `aggregate_items` fans it out
across every `discover_imports` folder and merges the result — this pins the
merge, the per-import failure isolation, and the truncation-cap disclosure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import load_app_module


@pytest.fixture(scope="module")
def store():
    return load_app_module("conversation-ingest", "store")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _queue_only_import(root: Path, key: str, items: list[dict]) -> Path:
    """A minimal import folder: queue-only, no audit file. `list_items`
    doesn't need an audit record to answer the `pending` bucket.
    """
    folder = root / key
    _write_json(
        folder / "coverage-queue.json",
        {
            "schema_version": 1,
            "provider": "provider-x",
            "total_unique": len(items),
            "processed": 0,
            "pending": len(items),
            "items": items,
        },
    )
    return folder


def test_aggregate_merges_across_imports_sorted_by_date_desc(store, tmp_path):
    _queue_only_import(
        tmp_path, "import-a",
        [{"provider_id": "a-1", "title": "A one", "created_at": "2024-01-01T00:00:00Z"}],
    )
    _queue_only_import(
        tmp_path, "import-b",
        [{"provider_id": "b-1", "title": "B one", "created_at": "2024-06-01T00:00:00Z"}],
    )
    result = store.aggregate_items(tmp_path, bucket="pending")
    assert result["import_key"] is None
    assert result["total"] == 2
    assert sorted(result["imports"]) == ["import-a", "import-b"]
    assert result["import_errors"] == []
    assert result["truncated_imports"] == []
    # Most-recent-first across the merged set, not grouped by import.
    assert [r["provider_id"] for r in result["rows"]] == ["b-1", "a-1"]
    # Every row carries which import it came from.
    assert {r["import_key"] for r in result["rows"]} == {"import-a", "import-b"}


def test_aggregate_isolates_a_broken_import(store, tmp_path):
    _queue_only_import(
        tmp_path, "import-good",
        [{"provider_id": "g-1", "title": "Good", "created_at": "2024-01-01"}],
    )
    broken = tmp_path / "import-broken"
    broken.mkdir()
    (broken / "coverage-queue.json").write_text("{not json", encoding="utf-8")

    result = store.aggregate_items(tmp_path, bucket="pending")
    assert result["total"] == 1
    assert result["rows"][0]["provider_id"] == "g-1"
    assert len(result["import_errors"]) == 1
    assert result["import_errors"][0]["import_key"] == "import-broken"


def test_aggregate_missing_root_is_fail_soft(store, tmp_path):
    result = store.aggregate_items(tmp_path / "does-not-exist", bucket="pending")
    assert result == {
        "import_key": None,
        "bucket": "pending",
        "query": "",
        "offset": 0,
        "limit": 50,
        "total": 0,
        "rows": [],
        "imports": [],
        "import_errors": [],
        "truncated_imports": [],
    }


def test_aggregate_pagination_slices_the_merged_set(store, tmp_path):
    for i in range(5):
        _queue_only_import(
            tmp_path, f"import-{i}",
            [{"provider_id": f"p-{i}", "title": f"Item {i}", "created_at": f"2024-01-0{i + 1}"}],
        )
    page1 = store.aggregate_items(tmp_path, bucket="pending", offset=0, limit=2)
    page2 = store.aggregate_items(tmp_path, bucket="pending", offset=2, limit=2)
    assert page1["total"] == 5
    assert len(page1["rows"]) == 2
    assert len(page2["rows"]) == 2
    # No overlap between pages.
    ids1 = {r["provider_id"] for r in page1["rows"]}
    ids2 = {r["provider_id"] for r in page2["rows"]}
    assert not (ids1 & ids2)


def test_aggregate_reports_truncation_instead_of_silently_dropping(store, tmp_path, monkeypatch):
    # Exercise the real MAX_PAGE_SIZE cap by shrinking it for this test rather
    # than constructing 200+ fixture items.
    monkeypatch.setattr(store, "MAX_PAGE_SIZE", 2)
    _queue_only_import(
        tmp_path, "import-big",
        [
            {"provider_id": "b-1", "title": "One", "created_at": "2024-01-01"},
            {"provider_id": "b-2", "title": "Two", "created_at": "2024-01-02"},
            {"provider_id": "b-3", "title": "Three", "created_at": "2024-01-03"},
        ],
    )
    result = store.aggregate_items(tmp_path, bucket="pending", limit=10)
    assert result["truncated_imports"] == ["import-big"]
    # Merge only ever sees what fit under the per-import cap (2 of 3).
    assert result["total"] == 2
