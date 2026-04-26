"""Unit tests for emptyos.sdk.BaseApp helpers.

Pure in-process — no daemon required. (server_health will skip when
daemon is down; these still run fine when it is up.)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from emptyos.sdk import BaseApp


def _mk_app(notes_path):
    """Build a BaseApp with a minimal fake kernel wired for the path bits."""
    config = MagicMock()
    config.notes_path = notes_path
    config.data_dir = Path("./data")
    services = MagicMock()
    services.get_optional.return_value = None
    kernel = SimpleNamespace(config=config, services=services, vault_map=MagicMock())
    manifest = SimpleNamespace(id="testapp")
    # Bypass BaseApp.__init__ — it does heavy wiring we don't need.
    app = BaseApp.__new__(BaseApp)
    app.kernel = kernel
    app.manifest = manifest
    return app


def test_vault_root_returns_configured_path():
    app = _mk_app(Path("/some/vault"))
    assert app.vault_root == Path("/some/vault")


def test_vault_root_fallback_when_unset():
    app = _mk_app(None)
    assert app.vault_root == Path(".")


def test_vault_dir_built_from_vault_root():
    app = _mk_app(Path("/some/vault"))
    assert app.vault_dir == Path("/some/vault/30_Resources/EmptyOS/testapp")


def test_repo_root_derives_from_config_path(tmp_path):
    app = _mk_app(None)
    app.kernel.config.path = tmp_path / "emptyos.toml"
    assert app.repo_root == tmp_path.resolve()


def test_repo_root_falls_back_to_cwd_when_config_missing():
    app = _mk_app(None)
    # Simulate an unusable config.path — property should not raise.
    app.kernel.config.path = None
    assert app.repo_root == Path.cwd()
