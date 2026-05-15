"""Unit tests for vault_project_* extensions added to BaseApp.

Pure in-process — exercises vault_project_get + read_sidecar + write_sidecar
without a daemon. Companion to test_sdk_base_app.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from emptyos.sdk import BaseApp


def _mk_app(notes_path: Path) -> BaseApp:
    config = MagicMock()
    config.notes_path = notes_path
    config.data_dir = Path("./data")
    services = MagicMock()
    services.get_optional.return_value = None
    kernel = SimpleNamespace(config=config, services=services, vault_map=MagicMock())
    manifest = SimpleNamespace(id="testapp")
    app = BaseApp.__new__(BaseApp)
    app.kernel = kernel
    app.manifest = manifest
    # Simulate VaultIndex: in-memory map of path -> frontmatter dict.
    app._fake_fm = {}
    app.vault_get_properties = lambda p: app._fake_fm.get(p)  # type: ignore[assignment]
    app.vault_update = MagicMock()
    app.emit = AsyncMock()
    return app


def test_vault_project_get_returns_fm_with_path(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["10_Projects/p1/p1.md"] = {"id": "p1", "name": "Demo"}
    out = app.vault_project_get("10_Projects/p1/p1.md")
    assert out == {"id": "p1", "name": "Demo", "_path": "10_Projects/p1/p1.md"}


def test_vault_project_get_returns_none_when_missing(tmp_path):
    app = _mk_app(tmp_path)
    assert app.vault_project_get("missing.md") is None


def test_read_sidecar_missing_project(tmp_path):
    app = _mk_app(tmp_path)
    out = app.vault_project_read_sidecar(
        project_path="absent.md", sidecar_path="absent.json"
    )
    assert out == {"error": "project not found"}


def test_read_sidecar_empty_returns_empty_list(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    out = app.vault_project_read_sidecar(project_path="p.md", sidecar_path="p.json")
    assert out == {"ok": True, "readings": []}


def test_read_sidecar_parses_keyed_payload(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    (tmp_path / "p.json").write_text(json.dumps({"readings": [{"a": 1, "rho_a": 2}]}))
    out = app.vault_project_read_sidecar(project_path="p.md", sidecar_path="p.json")
    assert out == {"ok": True, "readings": [{"a": 1, "rho_a": 2}]}


def test_read_sidecar_parses_bare_list(tmp_path):
    """Legacy sidecars may be a bare JSON list — must still parse."""
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    (tmp_path / "p.json").write_text(json.dumps([{"a": 1}, {"a": 2}]))
    out = app.vault_project_read_sidecar(project_path="p.md", sidecar_path="p.json")
    assert out == {"ok": True, "readings": [{"a": 1}, {"a": 2}]}


def test_read_sidecar_custom_key(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    (tmp_path / "g.json").write_text(json.dumps({"nodes": [{"x": 0}]}))
    out = app.vault_project_read_sidecar(
        project_path="p.md", sidecar_path="g.json", key="nodes"
    )
    assert out == {"ok": True, "nodes": [{"x": 0}]}


def test_read_sidecar_invalid_json_surfaces_error(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    (tmp_path / "p.json").write_text("{not json")
    out = app.vault_project_read_sidecar(project_path="p.md", sidecar_path="p.json")
    assert "error" in out and "cannot read sidecar" in out["error"]


def test_write_sidecar_missing_project_does_not_write(tmp_path):
    app = _mk_app(tmp_path)
    out = asyncio.run(
        app.vault_project_write_sidecar(
            project_id="p1", project_path="absent.md",
            sidecar_path="p.json", items=[{"a": 1}],
        )
    )
    assert out == {"error": "project not found"}
    assert not (tmp_path / "p.json").exists()
    app.emit.assert_not_called()


def test_write_sidecar_writes_bumps_emits(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    items = [{"a": 1.0, "rho_a": 50.0}, {"a": 2.0, "rho_a": 60.0}]
    out = asyncio.run(
        app.vault_project_write_sidecar(
            project_id="p1", project_path="p.md", sidecar_path="p.json",
            items=items, event_name="testapp:saved",
        )
    )
    assert out == {"ok": True, "n": 2}
    # Sidecar written under {key: items}
    written = json.loads((tmp_path / "p.json").read_text())
    assert written == {"readings": items}
    # updated bumped on project
    app.vault_update.assert_called_once()
    args, _ = app.vault_update.call_args
    assert args[0] == "p.md" and "updated" in args[1]
    # event emitted with id + n
    app.emit.assert_awaited_once_with("testapp:saved", {"id": "p1", "n": 2})


def test_write_sidecar_no_event_skips_emit(tmp_path):
    app = _mk_app(tmp_path)
    app._fake_fm["p.md"] = {"id": "p"}
    out = asyncio.run(
        app.vault_project_write_sidecar(
            project_id="p1", project_path="p.md",
            sidecar_path="p.json", items=[],
        )
    )
    assert out == {"ok": True, "n": 0}
    app.emit.assert_not_called()
