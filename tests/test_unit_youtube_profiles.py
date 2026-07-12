"""Pure named-profile path tests for the YouTube connector."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _client():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("youtube_client_profiles", root / "plugins" / "youtube" / "client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_profile_keeps_existing_token_filename(tmp_path):
    assert _client().token_path(tmp_path).name == "youtube-token.json"


def test_named_profile_has_its_own_token_filename(tmp_path):
    assert _client().token_path(tmp_path, "music").name == "youtube-music-token.json"


def test_named_profile_rejects_path_characters(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        _client().token_path(tmp_path, "../music")
