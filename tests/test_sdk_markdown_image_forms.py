"""Regression: both image embed forms resolve through assets_prefix.

Pins the contract that:
  - ![[foo.png]]            (Obsidian wikilink embed) → prefixed
  - ![alt](foo.png)         (standard markdown)       → prefixed AND alt preserved
  - ![alt](http://...)      (absolute URL)            → NOT prefixed
  - ![alt](assets/foo.png)  (already-prefixed)        → NOT double-prefixed
  - ![alt](media/foo.png)   (media path)              → NOT touched (builder handles)

The flip-back from `![[x.png]]` → `![alt](x.png)` on 2026-05-24 (publish.md /
learn.md / boards.md) is the use case driving this test.
"""
import pytest

from emptyos.sdk.markdown_render import HAS_MARKDOWN, render_markdown

pytestmark = pytest.mark.skipif(not HAS_MARKDOWN, reason="python-markdown missing")


def _render(content, **kw):
    """render_markdown returns (html, toc) — extract html for assertions."""
    out = render_markdown(content, **kw)
    return out[0] if isinstance(out, tuple) else out


def test_obsidian_embed_gets_prefix_no_alt():
    html = _render("![[foo.png]]", assets_prefix="assets/")
    assert 'src="assets/foo.png"' in html


def test_standard_markdown_image_gets_prefix_and_keeps_alt():
    html = _render(
        "![A descriptive alt text](showcase-boards-kanban.png)",
        assets_prefix="assets/",
    )
    assert 'src="assets/showcase-boards-kanban.png"' in html
    assert 'alt="A descriptive alt text"' in html


def test_absolute_url_not_prefixed():
    html = _render(
        "![logo](https://example.com/logo.png)",
        assets_prefix="assets/",
    )
    assert 'src="https://example.com/logo.png"' in html
    assert "assets/https" not in html


def test_already_prefixed_not_double_prefixed():
    html = _render(
        "![ok](assets/already.png)",
        assets_prefix="assets/",
    )
    assert 'src="assets/already.png"' in html
    assert "assets/assets/" not in html


def test_media_path_not_touched_at_root():
    """At root render, media/ paths stay relative — builder copies media/ to root."""
    html = _render(
        "![photo](media/snap.jpg)",
        assets_prefix="assets/",
    )
    assert 'src="media/snap.jpg"' in html
    assert "assets/media/" not in html


def test_media_path_gets_parent_prefix_from_posts_subdir():
    """When rendering from posts/, media/ gets rewritten to ../media/."""
    html = _render(
        "![photo](media/snap.jpg)",
        assets_prefix="../assets/",
    )
    assert 'src="../media/snap.jpg"' in html


def test_obsidian_embed_works_for_all_image_types():
    for ext in ("png", "jpg", "jpeg", "gif", "svg", "webp"):
        html = _render(f"![[pic.{ext}]]", assets_prefix="assets/")
        assert f'src="assets/pic.{ext}"' in html, f"failed for .{ext}"


def test_mixed_forms_in_same_document():
    md = (
        "Header text.\n\n"
        "![[obsidian-form.png]]\n\n"
        "Some prose.\n\n"
        "![standard form with alt](standard-form.png)\n"
    )
    html = _render(md, assets_prefix="assets/")
    assert 'src="assets/obsidian-form.png"' in html
    assert 'src="assets/standard-form.png"' in html
    assert 'alt="standard form with alt"' in html
