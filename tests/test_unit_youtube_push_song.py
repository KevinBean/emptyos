"""Pure parse/pick tests for the song release driver.

The driver's real upload path can't be tested (it needs a live channel), so the
value here is pinning the two pure functions that decide WHAT gets uploaded —
which is where a silent wrong-file bug would hide.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _driver():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "youtube_push_song", root / "scripts" / "youtube_push_song.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGE = """---
artist: "Unsaid Signal"
---

## Title
- **Title:** Still Mind 静心
- **Privacy:** private

## Description

```
A zen ambient piece.
Second line.
```

## Tags

```
zen ambient, meditation, 治愈音乐
```
"""


def test_parse_package_pulls_the_upload_spec(tmp_path):
    pkg = tmp_path / "release-package.md"
    pkg.write_text(PACKAGE, encoding="utf-8")
    spec = _driver().parse_package(pkg)
    assert spec["title"] == "Still Mind 静心"
    assert spec["privacy"] == "private"
    assert spec["description"].startswith("A zen ambient piece.")
    assert spec["tags"] == ["zen ambient", "meditation", "治愈音乐"]


def test_privacy_defaults_to_private_when_the_bullet_is_absent(tmp_path):
    pkg = tmp_path / "release-package.md"
    pkg.write_text("## Title\n- **Title:** X\n", encoding="utf-8")
    assert _driver().parse_package(pkg)["privacy"] == "private"


def test_master_pick_is_deterministic_and_skips_the_teaser(tmp_path):
    # glob order is filesystem-dependent; an unsorted [0] would upload a
    # different file on a different machine.
    for name in ("zz-shorts.mp4", "aa-master.mp4", "song-teaser.mp4"):
        (tmp_path / name).write_bytes(b"")
    picked = _driver().find_assets(tmp_path)["master"]
    assert picked.name == "aa-master.mp4"


def test_find_assets_is_empty_not_crashing_on_a_bare_dir(tmp_path):
    assets = _driver().find_assets(tmp_path)
    assert assets == {"master": None, "thumbnail": None, "subtitles": None}
