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


# ── safe_json ────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, body: bytes):
        self._body = body

    async def body(self) -> bytes:
        return self._body


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_safe_json_decodes_valid_body():
    req = _FakeRequest(b'{"a": 1, "b": "x"}')
    assert _run(BaseApp.safe_json(req)) == {"a": 1, "b": "x"}


def test_safe_json_returns_empty_on_empty_body():
    assert _run(BaseApp.safe_json(_FakeRequest(b""))) == {}


def test_safe_json_returns_empty_on_malformed_json():
    assert _run(BaseApp.safe_json(_FakeRequest(b"not json at all"))) == {}


def test_safe_json_handles_cp1252_em_dash():
    # Windows curl em-dash inheritance: byte 0x97 in cp1252 → "—"
    body = b'{"note": "hello \x97 world"}'
    assert _run(BaseApp.safe_json(req := _FakeRequest(body))) == {"note": "hello — world"}
