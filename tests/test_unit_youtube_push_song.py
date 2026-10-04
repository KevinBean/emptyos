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


# --- video language (2026-10-04: an upload left defaultLanguage empty) --------

def test_language_labels_map_to_bcp47_tags():
    d = _driver()
    assert d.language_tag("Chinese (Traditional)") == "zh-Hant"
    assert d.language_tag("Chinese (Simplified)") == "zh-Hans"
    assert d.language_tag("English") == "en"
    assert d.language_tag("zh-Hant") == "zh-Hant"     # a bare tag passes through
    assert d.language_tag("") == ""                    # no bullet: leave unset
    assert d.language_tag("Klingon") is None           # unknown: refuse, never guess


def test_audio_language_drops_the_script_subtag():
    d = _driver()
    assert d.audio_language("zh-Hant") == "zh"
    assert d.audio_language("en") == "en"
    assert d.audio_language("") == ""


def test_parse_package_reads_the_language_bullet(tmp_path):
    pkg = tmp_path / "release-package.md"
    pkg.write_text("- **Title:** X\n- **Language:** Chinese (Traditional)\n", encoding="utf-8")
    assert _driver().parse_package(pkg)["language"] == "Chinese (Traditional)"


class _FakeClient:
    """Stands in for plugins/youtube/client.py; records what main() uploads."""

    def __init__(self):
        self.uploaded = None
        self.caption = None

    def list_uploaded_titles(self, creds):
        return set()

    def upload_video(self, creds, path, **kw):
        self.uploaded = kw
        return {"id": "vid", "url": "https://youtu.be/vid"}

    def set_thumbnail(self, *a, **kw):
        pass

    def upload_caption(self, creds, video_id, path, *, language):
        self.caption = language


def _run_main(monkeypatch, tmp_path, language_line, *argv):
    d = _driver()
    rel = tmp_path / "release"
    rel.mkdir(parents=True)
    (rel / "release-package.md").write_text(
        f"- **Title:** Song\n- **Privacy:** Private\n{language_line}", encoding="utf-8")
    (rel / "master.mp4").write_bytes(b"")
    (rel / "subs.srt").write_text("1\n", encoding="utf-8")
    fake = _FakeClient()
    monkeypatch.setattr(d.yt, "load_client", lambda: fake)
    monkeypatch.setattr(d.yt, "connect", lambda client, profile: object())
    monkeypatch.setattr(d.yt, "guard_channel", lambda *a, **kw: True)
    monkeypatch.setattr(d.living_master, "release_block_reason", lambda *a, **kw: None)
    monkeypatch.setattr(d, "review_master", lambda master: True)
    monkeypatch.setattr("sys.argv", ["youtube_push_song.py", str(tmp_path), "--yes", *argv])
    return d.main(), fake


def test_upload_sets_video_and_audio_language_from_the_package(monkeypatch, tmp_path):
    code, fake = _run_main(monkeypatch, tmp_path, "- **Language:** Chinese (Traditional)\n")
    assert code == 0
    assert fake.uploaded["language"] == "zh-Hant"
    assert fake.uploaded["audio_language"] == "zh"
    assert fake.caption == "zh-Hant"


def test_language_flag_overrides_the_package(monkeypatch, tmp_path):
    code, fake = _run_main(monkeypatch, tmp_path, "- **Language:** English\n",
                           "--language", "zh-Hans")
    assert code == 0
    assert (fake.uploaded["language"], fake.uploaded["audio_language"]) == ("zh-Hans", "zh")


def test_no_language_bullet_leaves_video_language_unset(monkeypatch, tmp_path):
    # Older packages carry no Language bullet: the video fields stay unset and
    # the caption keeps the previous zh-Hans default (an empty caption language
    # is rejected by the API).
    code, fake = _run_main(monkeypatch, tmp_path, "")
    assert code == 0
    assert (fake.uploaded["language"], fake.uploaded["audio_language"]) == ("", "")
    assert fake.caption == "zh-Hans"


def test_language_flag_is_validated_like_the_bullet(monkeypatch, tmp_path):
    code, fake = _run_main(monkeypatch, tmp_path, "", "--language", "Chinese (Traditional)")
    assert code == 0 and fake.uploaded["language"] == "zh-Hant"
    code, fake = _run_main(monkeypatch, tmp_path / "b", "", "--language", "Klingon")
    assert code == 1 and fake.uploaded is None


def test_unknown_language_label_refuses_before_any_upload(monkeypatch, tmp_path):
    code, fake = _run_main(monkeypatch, tmp_path, "- **Language:** Klingon\n")
    assert code == 1
    assert fake.uploaded is None
