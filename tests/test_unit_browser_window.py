"""Unit tests for the desktop-window helper + launcher (Phase 1 daily driver).

Pure-logic only — no daemon, no browser spawn. Safe in CI (Linux) where no
Chromium browser is installed: find_chromium() may legitimately return None.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from emptyos.sdk import browser_window as bw

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_eos_desktop():
    """Load scripts/eos_desktop.py as a module (it's not on the import path)."""
    spec = importlib.util.spec_from_file_location(
        "eos_desktop", REPO_ROOT / "scripts" / "eos_desktop.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── browser_window.build_app_window_args (pure) ────────────────────────────

def test_build_args_shape():
    args = bw.build_app_window_args(
        "/usr/bin/chrome", "http://127.0.0.1:9000/",
        width=1440, height=900, profile_dir="/tmp/prof",
    )
    assert args[0] == "/usr/bin/chrome"
    assert "--app=http://127.0.0.1:9000/" in args
    assert "--window-size=1440,900" in args
    assert any(a.startswith("--user-data-dir=") and a.endswith("prof") for a in args)


def test_build_args_includes_base_flags():
    args = bw.build_app_window_args(
        "chrome", "http://x/", profile_dir="/tmp/p",
    )
    for flag in ("--no-first-run", "--no-default-browser-check", "--disable-features=TranslateUI"):
        assert flag in args


def test_build_args_extra_args_appended_last():
    args = bw.build_app_window_args(
        "chrome", "http://x/", profile_dir="/tmp/p", extra_args=("--kiosk",),
    )
    assert args[-1] == "--kiosk"


def test_build_args_coerces_int_size():
    args = bw.build_app_window_args(
        "chrome", "http://x/", width="800", height="600", profile_dir="/tmp/p",  # type: ignore[arg-type]
    )
    assert "--window-size=800,600" in args


# ── browser_window.find_chromium (shape only — env-dependent) ──────────────

def test_find_chromium_shape():
    result = bw.find_chromium()
    assert result is None or (
        isinstance(result, tuple) and len(result) == 2
        and all(isinstance(x, str) for x in result)
    )


# ── eos_desktop pure helpers ───────────────────────────────────────────────

def test_network_config_defaults_on_empty():
    eos = _load_eos_desktop()
    assert eos.network_config_from_dict({}) == {"port": 9000}


def test_network_config_reads_port():
    eos = _load_eos_desktop()
    assert eos.network_config_from_dict({"network": {"port": 9100}}) == {"port": 9100}


def test_network_config_coerces_bad_port():
    eos = _load_eos_desktop()
    assert eos.network_config_from_dict({"network": {"port": "nope"}})["port"] == 9000


def test_build_window_url_default():
    eos = _load_eos_desktop()
    assert eos.build_window_url(9000) == "http://127.0.0.1:9000/"


def test_build_window_url_code_path():
    eos = _load_eos_desktop()
    assert eos.build_window_url(9000, path="/code/") == "http://127.0.0.1:9000/code/"


def test_build_window_url_has_no_token_param():
    # Security regression guard: auth_token must never be placeable in the URL
    # (it leaks into browser argv + history). build_window_url has no token arg.
    import inspect
    eos = _load_eos_desktop()
    assert "token" not in inspect.signature(eos.build_window_url).parameters
