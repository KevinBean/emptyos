"""Pure-logic unit test for BoardsApp._sync_presets — the app-contributed
vault_tag auto-materialization fix (2026-08-22).

Regression for a real gap found via live sandbox verification of the
collection-app pipeline: `_sync_presets` only ever auto-registered `app`/
`mixed`-sourced presets as saved boards. A `vault_tag`-sourced preset
contributed by an app (e.g. `emptyos.sdk.collection_app.CollectionApp.
board_presets`) was silently skipped — the board never appeared without a
manual click-through. Fixed by tracking which preset ids came from
`call_contributions` (vs. the static built-in PRESETS dict) and
auto-materializing those regardless of source type, while leaving the
static vault_tag templates (crm-pipeline, bug-tracker, ...) as
template-gallery-only, unchanged, and never forcing a contributed vault_tag
board read-only (no source app to route writes through).

Loads apps.boards standalone via the package-shim pattern
(tests/test_unit_rooms_logic.py is the reference), so no daemon is needed.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import app_path


@pytest.fixture(scope="module")
def BoardsApp():
    repo_root = Path(__file__).resolve().parent.parent
    boards_dir = app_path("boards")
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.boards" not in sys.modules:
        boards_pkg = types.ModuleType("apps.boards")
        boards_pkg.__path__ = [str(boards_dir)]
        sys.modules["apps.boards"] = boards_pkg

    # Preload sibling helper modules first so app.py's `from . import X` /
    # `from .X import Y` relative imports resolve (same reasoning as the
    # rooms decomposition — see .claude/rules/multi-module-apps.md).
    for sub in (
        "items", "links", "saved_views", "activity", "attachments",
        "comments", "planner_sync", "board_engine", "link_index",
        "presets", "views", "automation",
    ):
        mod_name = f"apps.boards.{sub}"
        if mod_name in sys.modules:
            continue
        sub_spec = importlib.util.spec_from_file_location(mod_name, boards_dir / f"{sub}.py")
        sub_mod = importlib.util.module_from_spec(sub_spec)
        sys.modules[mod_name] = sub_mod
        sub_spec.loader.exec_module(sub_mod)

    spec = importlib.util.spec_from_file_location("apps.boards.app", boards_dir / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.boards.app"] = mod
    spec.loader.exec_module(mod)
    return mod.BoardsApp


class _FakeAppLoader:
    """Empty manifests dict — the force-load-contributors loop becomes a
    no-op, since the test drives `call_contributions` directly instead."""

    manifests: dict = {}
    instances: dict = {}


def _fake_self(tmp_path: Path, contributed_presets: list[dict]):
    """A duck-typed `self` for BoardsApp._sync_presets — the method only
    touches kernel.apps, call_contributions, and self._store."""
    from apps.boards.board_engine import BoardConfigStore

    vault_app = SimpleNamespace(
        kernel=SimpleNamespace(config=SimpleNamespace(notes_path=tmp_path)),
        vault_config=lambda key, default="": default,
        vault_config_path=lambda key, default="": tmp_path / default,
    )
    store = BoardConfigStore(vault_app)

    async def call_contributions(target, slot, **kwargs):
        assert (target, slot) == ("boards", "preset")
        return [({"app": "fake"}, p) for p in contributed_presets]

    return SimpleNamespace(
        kernel=SimpleNamespace(apps=_FakeAppLoader()),
        call_contributions=call_contributions,
        _store=store,
    )


def test_contributed_vault_tag_preset_is_auto_materialized_and_editable(tmp_path, BoardsApp):
    contributed = [{
        "id": "widget-tracker-collection",
        "name": "Widget Tracker",
        "description": "Items owned by Widget Tracker.",
        "source_tag": "widget-tracker",
        "columns": [{"id": "title", "label": "Title", "type": "text"}],
        "views": [{"type": "table", "default": True}],
    }]
    fake_self = _fake_self(tmp_path, contributed)
    asyncio.run(BoardsApp._sync_presets(fake_self))

    saved = fake_self._store.get_board("widget-tracker-collection")
    assert saved is not None
    assert saved["readonly"] is False  # no source app — never forced read-only
    assert saved["source_app_id"] == ""
    assert saved["source_tag"] == "widget-tracker"


def test_static_vault_tag_preset_stays_template_gallery_only(tmp_path, BoardsApp):
    """The built-in PRESETS dict (crm-pipeline, bug-tracker, ...) must NOT
    change behavior — still template-only, not auto-saved."""
    fake_self = _fake_self(tmp_path, contributed_presets=[])
    asyncio.run(BoardsApp._sync_presets(fake_self))

    assert fake_self._store.get_board("crm-pipeline") is None
    assert fake_self._store.get_board("bug-tracker") is None


def test_app_sourced_static_preset_still_auto_materializes_readonly(tmp_path, BoardsApp):
    """Regression: the pre-existing app/mixed auto-materialize path (e.g.
    project-tracker, sourced from the `projects` app) must stay unchanged —
    always auto-saved, always forced read-only."""
    fake_self = _fake_self(tmp_path, contributed_presets=[])
    asyncio.run(BoardsApp._sync_presets(fake_self))

    saved = fake_self._store.get_board("project-tracker")
    assert saved is not None
    assert saved["readonly"] is True
    assert saved["source_app_id"] == "projects"


def test_contributed_app_sourced_preset_is_still_forced_readonly(tmp_path, BoardsApp):
    """A contributed preset that IS app-sourced (e.g. nest's residences/
    rooms boards) keeps the original force-readonly behavior — only the
    vault_tag case changed."""
    contributed = [{
        "id": "nest-residences",
        "name": "Residences",
        "source": {"type": "app", "app": "nest", "method": "list_all"},
        "columns": [{"id": "name", "label": "Name", "type": "text"}],
        "views": [{"type": "table", "default": True}],
    }]
    fake_self = _fake_self(tmp_path, contributed)
    asyncio.run(BoardsApp._sync_presets(fake_self))

    saved = fake_self._store.get_board("nest-residences")
    assert saved is not None
    assert saved["readonly"] is True
    assert saved["source_app_id"] == "nest"


def test_resync_on_second_boot_preserves_readonly_semantics(tmp_path, BoardsApp):
    """A second `_sync_presets` call (simulating a restart) re-syncs
    structural fields without flipping readonly on an already-saved
    contributed vault_tag board."""
    contributed = [{
        "id": "widget-tracker-collection",
        "name": "Widget Tracker",
        "source_tag": "widget-tracker",
        "columns": [{"id": "title", "label": "Title", "type": "text"}],
        "views": [{"type": "table", "default": True}],
    }]
    fake_self = _fake_self(tmp_path, contributed)
    asyncio.run(BoardsApp._sync_presets(fake_self))
    asyncio.run(BoardsApp._sync_presets(fake_self))

    saved = fake_self._store.get_board("widget-tracker-collection")
    assert saved["readonly"] is False
