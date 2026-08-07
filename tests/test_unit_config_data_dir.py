"""Unit: Config.data_dir must be absolute.

A relative data_dir silently changes meaning when handed to a helper that
resolves relative paths against the vault — BaseApp.render_pdf does, so the
worklog timesheet export wrote its PDF into <vault>/data/apps/worklog/exports/
while serve_data_file looked under the repo. The download 404'd and the user's
vault collected stray PDFs, with no error on either side.
"""
from pathlib import Path

from emptyos.kernel.config import Config


def _config(tmp_path, value=None):
    cfg = Config.__new__(Config)
    data = {"os": {"data_dir": value}} if value is not None else {}
    cfg._data = data
    cfg.path = tmp_path / "emptyos.toml"
    return cfg


def test_default_data_dir_is_absolute(tmp_path):
    assert _config(tmp_path).data_dir.is_absolute()


def test_relative_configured_value_is_resolved(tmp_path):
    got = _config(tmp_path, "./data").data_dir
    assert got.is_absolute()
    assert got == (tmp_path / "data").resolve()


def test_absolute_configured_value_is_left_alone(tmp_path):
    target = (tmp_path / "elsewhere" / "state").resolve()
    assert _config(tmp_path, str(target)).data_dir == target


def test_joining_onto_a_vault_path_cannot_smuggle_it_into_the_vault(tmp_path):
    """The exact failure mode: `vault / data_dir_derived_path`.

    With an absolute data_dir, pathlib's join returns the absolute operand, so
    a vault-relative resolver can no longer land inside the vault.
    """
    vault = (tmp_path / "vault").resolve()
    out = _config(tmp_path).data_dir / "apps" / "worklog" / "exports" / "t.pdf"
    assert (vault / out) == out, "absolute path must win the join"
    assert vault not in Path(vault / out).parents


def test_get_falls_back_when_section_missing(tmp_path):
    cfg = Config.__new__(Config)
    cfg._data = {}
    cfg.path = tmp_path / "emptyos.toml"
    assert cfg.data_dir == (tmp_path / "data").resolve()


def test_anchored_to_the_config_file_not_the_cwd(tmp_path, monkeypatch):
    """A relative value follows the config file, not wherever it was launched.

    `emptyos/cli/sandbox_target.py` re-anchored a relative data_dir to the
    config's directory by hand, and its test drives `Config` over a tmp config
    while the CWD is the repo — a CWD anchor silently read the repo's real
    pool.json instead. That test is the regression pin for this.
    """
    cfg = _config(tmp_path)
    sub = tmp_path / "elsewhere"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert cfg.data_dir == (tmp_path / "data").resolve()
