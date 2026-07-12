"""Unit tests for `_inject_asset_versions` — automatic page-asset cache-busting.

Pins the fix for the stale-bundle class of bug (hub launcher rendering empty
cards after hub.js changed but a service worker kept the old copy): the daemon
stamps `?v=<mtime>` onto an app's own /pages/*.js|css tags so every edit is a
new URL the cache can't pin. Pure import (no kernel boot).
"""

from pathlib import Path

from emptyos.web.server import _inject_asset_versions


def _make_pages(tmp_path: Path) -> Path:
    pages = tmp_path / "pages"
    (pages / "nested").mkdir(parents=True)
    (pages / "app.js").write_text("// js", encoding="utf-8")
    (pages / "app.css").write_text("/* css */", encoding="utf-8")
    (pages / "nested" / "child.js").write_text("// child", encoding="utf-8")
    return pages


def test_stamps_own_pages_js_and_css(tmp_path):
    pages = _make_pages(tmp_path)
    js_v = int((pages / "app.js").stat().st_mtime)
    css_v = int((pages / "app.css").stat().st_mtime)
    html = (
        '<link href="/myapp/pages/app.css">'
        '<script src="/myapp/pages/app.js"></script>'
    )
    out = _inject_asset_versions(html, "/myapp", pages)
    assert f"/myapp/pages/app.js?v={js_v}" in out
    assert f"/myapp/pages/app.css?v={css_v}" in out


def test_nested_asset_resolved_by_relative_path(tmp_path):
    pages = _make_pages(tmp_path)
    child_v = int((pages / "nested" / "child.js").stat().st_mtime)
    html = '<script src="/myapp/pages/nested/child.js"></script>'
    out = _inject_asset_versions(html, "/myapp", pages)
    assert f"/myapp/pages/nested/child.js?v={child_v}" in out


def test_static_bundles_untouched(tmp_path):
    pages = _make_pages(tmp_path)
    html = '<script src="/static/eos.js"></script>'
    out = _inject_asset_versions(html, "/myapp", pages)
    assert out == html  # shared /static/* is the service worker's job


def test_missing_file_left_bare(tmp_path):
    pages = _make_pages(tmp_path)
    html = '<script src="/myapp/pages/gone.js"></script>'
    out = _inject_asset_versions(html, "/myapp", pages)
    assert "gone.js?v" not in out
    assert '/myapp/pages/gone.js"' in out


def test_idempotent(tmp_path):
    pages = _make_pages(tmp_path)
    html = '<script src="/myapp/pages/app.js"></script>'
    once = _inject_asset_versions(html, "/myapp", pages)
    twice = _inject_asset_versions(once, "/myapp", pages)
    assert once == twice  # already-stamped URLs carry a '?' and don't re-match


def test_only_this_apps_prefix_stamped(tmp_path):
    pages = _make_pages(tmp_path)
    # An asset referenced under a different app's prefix is not ours to stamp.
    html = '<script src="/otherapp/pages/app.js"></script>'
    out = _inject_asset_versions(html, "/myapp", pages)
    assert out == html


def test_empty_or_missing_inputs_noop(tmp_path):
    pages = _make_pages(tmp_path)
    assert _inject_asset_versions("", "/myapp", pages) == ""
    assert _inject_asset_versions("<x/>", "", pages) == "<x/>"
    assert _inject_asset_versions("<x/>", "/myapp", None) == "<x/>"
