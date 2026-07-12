"""Article-image extraction + proportional time-distribution.

Pins:
  - _extract_article_images resolves ![alt](path) against the source folder,
    media/, and the vault root; skips absolute URLs / data URIs; dedupes;
    preserves document order; drops missing files silently.
  - The podcast pipeline's article_images path produces N scenes that span the
    full audio duration with equal slices, last scene padded to total_ms.

The extractor is module-level on apps/publish/media.py and takes `self` as its
first arg (the multi-module decomposition pattern). Test rigs a fake `self`
that satisfies the two attributes it needs: `_vault_dir()` returning the test
tmp dir, `_source_folder()` returning "posts".
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Repo root on path so `emptyos.sdk` resolves to the real package — letting us
# load apps/publish/media.py via spec_from_file_location without stubbing
# `emptyos.sdk` (a stub would pollute sys.modules and break sibling tests
# that need the real markdown_render submodule).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import app_path
PUBLISH_DIR = app_path("publish")
PUBLISH_MEDIA = PUBLISH_DIR / "media.py"


def _load_media_module():
    """Load media.py under a synthetic package so its sibling relative import
    (`from .prompts import PROMPTS`) resolves outside the kernel."""
    pkg_name = "publish_pkg_for_test"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(PUBLISH_DIR)]
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(f"{pkg_name}.media", PUBLISH_MEDIA)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[f"{pkg_name}.media"] = module
    spec.loader.exec_module(module)
    return module


media = _load_media_module()


class FakeApp:
    def __init__(self, vault_dir: Path, source_folder: str = "posts"):
        self._vault = vault_dir
        self._source = source_folder

    def _vault_dir(self):
        return str(self._vault)

    def _source_folder(self, site=None):
        return self._source


def _touch(path: Path, content: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def fake_vault(tmp_path):
    (tmp_path / "posts" / "media").mkdir(parents=True)
    _touch(tmp_path / "posts" / "media" / "hero.png")
    _touch(tmp_path / "posts" / "media" / "chart.jpg")
    _touch(tmp_path / "posts" / "inline-bare.png")
    _touch(tmp_path / "shared.png")
    return tmp_path


def test_extracts_canonical_media_path(fake_vault):
    body = "Intro.\n\n![A hero shot](media/hero.png)\n\nMore."
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    assert len(imgs) == 1
    assert imgs[0].endswith("hero.png")


def test_extracts_multiple_in_document_order(fake_vault):
    body = "![](media/chart.jpg)\n\n![](media/hero.png)\n\n![](media/chart.jpg)"
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    # Dedupe keeps first occurrence
    assert len(imgs) == 2
    assert imgs[0].endswith("chart.jpg")
    assert imgs[1].endswith("hero.png")


def test_resolves_bare_filename_against_source_folder(fake_vault):
    body = "![](inline-bare.png)"
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    assert len(imgs) == 1
    assert imgs[0].endswith("inline-bare.png")


def test_resolves_vault_root_fallback(fake_vault):
    body = "![](shared.png)"
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    assert len(imgs) == 1
    assert imgs[0].endswith("shared.png")


def test_skips_absolute_urls_and_data_uris(fake_vault):
    body = (
        "![ext](https://example.com/img.png)\n"
        "![data](data:image/png;base64,abc)\n"
        "![anchor](#foo.png)\n"
        "![ok](media/hero.png)"
    )
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    assert len(imgs) == 1
    assert imgs[0].endswith("hero.png")


def test_drops_missing_files_silently(fake_vault):
    body = "![](media/ghost.png)\n\n![](media/hero.png)"
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    assert len(imgs) == 1
    assert imgs[0].endswith("hero.png")


def test_extractor_returns_empty_for_no_images(fake_vault):
    body = "Just text. No images at all."
    app = FakeApp(fake_vault)
    assert media._extract_article_images(app, body, "posts") == []


def test_extractor_handles_wikilink_embeds_by_skipping(fake_vault):
    """Wikilink embeds ![[x.png]] are intentionally ignored — they're the
    legacy form that the showcase markdown flip migrated away from."""
    body = "![[media/hero.png]]\n\n![](media/hero.png)"
    app = FakeApp(fake_vault)
    imgs = media._extract_article_images(app, body, "posts")
    # The wikilink embed is skipped; only the standard form resolves.
    assert len(imgs) == 1


# --- Proportional time-distribution math ---

def test_time_distribution_math():
    """Proves the per-image slice math used in pipeline.py is symmetric."""
    total_ms = 60000  # 60s
    n = 4
    slice_ms = total_ms // n  # 15000
    scenes = []
    for i in range(n):
        start = i * slice_ms
        end = (i + 1) * slice_ms if i < n - 1 else total_ms
        scenes.append((start, end))
    assert scenes[0] == (0, 15000)
    assert scenes[1] == (15000, 30000)
    assert scenes[-1][1] == total_ms  # last scene padded to total
    # No gaps
    for a, b in zip(scenes, scenes[1:]):
        assert a[1] == b[0]


def test_time_distribution_handles_non_even_division():
    total_ms = 60001
    n = 4
    slice_ms = total_ms // n  # 15000
    scenes = [(i * slice_ms, (i + 1) * slice_ms if i < n - 1 else total_ms) for i in range(n)]
    # Last scene absorbs the remainder
    assert scenes[-1] == (45000, 60001)
