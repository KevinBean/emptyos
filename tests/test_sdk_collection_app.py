"""Unit tests for emptyos.sdk.collection_app — CollectionLibrary + CollectionApp.

Pure in-process — no daemon required. Mirrors the test_sdk_vault_library.py
mock-app pattern. Pins two real bugs found during live sandbox verification
of the collection-app scaffolding pipeline (2026-08-22):
  - a partial PUT update demanded every required field, not just the ones
    actually submitted (coerce_and_validate's require_missing flag);
  - delete() didn't re-poke the vault index, so a deleted item lingered in
    list() until the next restart.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from emptyos.sdk.collection_app import CollectionApp, CollectionLibrary

SCHEMA = [
    {"id": "title", "label": "Title", "type": "text", "required": True},
    {
        "id": "status", "label": "Status", "type": "select", "required": True,
        "options": ["todo", "doing", "done"], "default": "todo",
    },
    {"id": "notes", "label": "Notes", "type": "text"},
]

COLLECTION_CONFIG = {"tag": "widget", "fields": SCHEMA}


class _FakeManifest:
    id = "widget-tracker"
    name = "Widget Tracker"
    raw = {"collection": COLLECTION_CONFIG}


def _mk_app(tmp_path: Path):
    config = MagicMock()
    config.notes_path = tmp_path
    services = MagicMock()
    services.get_optional.return_value = None  # no VaultIndex → fallback scan
    kernel = SimpleNamespace(config=config, services=services)
    created: list[tuple[str, dict, str]] = []
    emitted: list[tuple[str, dict]] = []

    def _vault_create_note(rel_path, fm, body=""):
        p = tmp_path / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                lines.append(f"{k}:")
                lines.extend(f"  - {item}" for item in v)
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        p.write_text("\n".join(lines) + "\n\n" + body, encoding="utf-8")
        created.append((rel_path, fm, body))

    async def _emit(event_type, data=None):
        emitted.append((event_type, data or {}))

    app = SimpleNamespace(
        kernel=kernel,
        manifest=_FakeManifest(),
        vault_create_note=_vault_create_note,
        emit=_emit,
        _created=created,
        _emitted=emitted,
    )
    return app


def _mk_lib(tmp_path: Path) -> CollectionLibrary:
    return CollectionLibrary(_mk_app(tmp_path), COLLECTION_CONFIG)


# ── CollectionLibrary.__init__ ──────────────────────────────────────────


def test_fields_built_from_schema_types(tmp_path):
    lib = _mk_lib(tmp_path)
    assert lib.fields == {"title": str, "status": str, "notes": str}
    assert lib.tag == "widget"
    assert lib.fallback_folder == "30_Resources/EmptyOS/widget-tracker"


# ── coerce_and_validate ──────────────────────────────────────────────────


def test_create_shape_flags_missing_required(tmp_path):
    lib = _mk_lib(tmp_path)
    clean, errors = lib.coerce_and_validate({"status": "doing"})
    assert errors == ["Title is required"]
    assert "title" not in clean


def test_create_shape_accepts_full_payload(tmp_path):
    lib = _mk_lib(tmp_path)
    clean, errors = lib.coerce_and_validate({"title": "Widget A", "status": "doing"})
    assert errors == []
    assert clean == {"title": "Widget A", "status": "doing"}


def test_partial_update_does_not_demand_untouched_required_fields(tmp_path):
    """Regression: a PUT that only changes `status` must not fail because
    `title` (required) wasn't part of the diff."""
    lib = _mk_lib(tmp_path)
    clean, errors = lib.coerce_and_validate({"status": "done"}, require_missing=False)
    assert errors == []
    assert clean == {"status": "done"}


def test_partial_update_still_rejects_explicit_empty_required_value(tmp_path):
    lib = _mk_lib(tmp_path)
    clean, errors = lib.coerce_and_validate({"title": ""}, require_missing=False)
    assert errors == ["Title is required"]


# ── create() ─────────────────────────────────────────────────────────────


def test_create_writes_vault_note_and_applies_select_default(tmp_path):
    app = _mk_app(tmp_path)
    lib = CollectionLibrary(app, COLLECTION_CONFIG)
    import asyncio

    result = asyncio.run(lib.create({"title": "Widget A"}))
    assert result["ok"] is True
    assert result["file"] == "widget-a.md"
    rel_path, fm, _body = app._created[0]
    assert fm["status"] == "todo"  # select default applied
    assert fm["tags"] == ["widget"]
    assert app._emitted == [("widget:created", {"file": "widget-a.md", "path": rel_path})]


def test_create_refuses_missing_required(tmp_path):
    app = _mk_app(tmp_path)
    lib = CollectionLibrary(app, COLLECTION_CONFIG)
    import asyncio

    result = asyncio.run(lib.create({"status": "doing"}))
    assert result == {"error": "Title is required"}
    assert app._created == []


# ── update_validated() ───────────────────────────────────────────────────


def test_update_validated_partial_update_succeeds(tmp_path):
    """Regression for the live-verified PUT bug: updating only `status`
    on an existing item must succeed without re-supplying `title`."""
    app = _mk_app(tmp_path)
    lib = CollectionLibrary(app, COLLECTION_CONFIG)
    import asyncio

    asyncio.run(lib.create({"title": "Widget A"}))
    result = asyncio.run(lib.update_validated("widget-a.md", {"status": "done"}))
    assert result.get("ok") is True
    detail = lib.detail("widget-a.md")
    assert detail["status"] == "done"
    assert detail["title"] == "Widget A"  # untouched field preserved


def test_update_validated_refuses_no_valid_fields(tmp_path):
    app = _mk_app(tmp_path)
    lib = CollectionLibrary(app, COLLECTION_CONFIG)
    import asyncio

    asyncio.run(lib.create({"title": "Widget A"}))
    result = asyncio.run(lib.update_validated("widget-a.md", {"unknown_field": "x"}))
    assert result == {"error": "no valid fields to update"}


# ── delete() ──────────────────────────────────────────────────────────────


def test_delete_removes_file_and_pokes_index(tmp_path):
    """Regression for the live-verified delete bug: the file must actually
    disappear from disk, and _poke_index must be called with the deleted
    path (VaultIndex.index_file removes stale entries when the path no
    longer exists — see emptyos/runtime/vault_index.py)."""
    app = _mk_app(tmp_path)
    lib = CollectionLibrary(app, COLLECTION_CONFIG)
    import asyncio

    asyncio.run(lib.create({"title": "Widget A"}))
    path = lib.find_file("widget-a.md")
    assert path is not None and path.exists()

    poked: list[Path] = []
    lib._poke_index = lambda p: poked.append(p)  # type: ignore[method-assign]

    result = asyncio.run(lib.delete("widget-a.md"))
    assert result == {"ok": True}
    assert not path.exists()
    assert poked == [path]
    assert app._emitted[-1] == ("widget:deleted", {"file": "widget-a.md"})


def test_delete_missing_file_returns_error(tmp_path):
    lib = _mk_lib(tmp_path)
    import asyncio

    assert asyncio.run(lib.delete("nope.md")) == {"error": "not found"}


# ── CollectionApp.board_presets() ────────────────────────────────────────


class _FakeCollectionApp(CollectionApp):
    manifest = _FakeManifest()


def test_board_presets_shape():
    app = _FakeCollectionApp()
    preset = app.board_presets()
    assert preset["id"] == "widget-tracker-collection"
    assert preset["source_tag"] == "widget"
    assert [c["id"] for c in preset["columns"]] == ["title", "status", "notes"]
    assert preset["kanban_group_by"] == "status"  # first select column


def test_board_presets_none_without_tag():
    class _NoTagManifest:
        id = "x"
        name = "X"
        raw = {"collection": {"fields": SCHEMA}}  # no tag

    class _App(CollectionApp):
        manifest = _NoTagManifest()

    assert _App().board_presets() is None
