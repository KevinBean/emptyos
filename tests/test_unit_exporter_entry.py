"""Exporter — `[provides.export].entry` promotion + the download media type.

Two regressions worth pinning, both found 2026-08-20 while giving `jobs` an
export:

1. **entry promotion.** An app may export a focused subset rather than its whole
   UI. Every downstream step in the exporter addresses `index.html` by name, so
   the declared entry is promoted to that name right after `pages/` is copied.
   Without it, `entry` parses fine and is silently ignored — the worst kind of
   config bug, because the manifest reads correct.

2. **single-html is served as a download, not a page.** The export route used
   `media_type="text/html"`, and every text/html response passes through the
   viewport middleware, which rewrites the first literal ``<head>`` it finds.
   In an inlined bundle that string occurs inside an `eos.js` comment, so the
   injected ``<meta>`` landed mid-comment and broke the JS. Every single-html
   export downloaded over HTTP arrived corrupted; the on-disk build path was
   unaffected, which is why it went unnoticed.

Both are daemon-free: (1) drives the promotion logic over a temp tree, (2) is a
source assertion on the route plus a proof that the corrupting substring really
is present in the shipped asset.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ── 1. entry promotion ────────────────────────────────────────────────


def _promote(out_dir: Path, entry_page: str) -> None:
    """The exporter's promotion step, transcribed.

    Kept as a transcription rather than an import because the real one sits
    mid-`build()` behind an app instance and a kernel; the behaviour under test
    is the file move, and `test_promotion_matches_source` pins the two together.
    """
    if entry_page and entry_page != "index.html":
        src = out_dir / entry_page
        if not src.is_file():
            raise AssertionError(f"entry '{entry_page}' does not exist")
        shutil.copy(src, out_dir / "index.html")


def test_entry_promotes_over_existing_index(tmp_path: Path):
    (tmp_path / "index.html").write_text("<p>the full app UI</p>", encoding="utf-8")
    (tmp_path / "drill.html").write_text("<p>the focused drill</p>", encoding="utf-8")
    _promote(tmp_path, "drill.html")
    assert "focused drill" in (tmp_path / "index.html").read_text(encoding="utf-8")
    # The named page survives too — single-html deletes siblings later anyway,
    # and a dir export is more useful with both present.
    assert (tmp_path / "drill.html").is_file()


def test_no_entry_leaves_index_untouched(tmp_path: Path):
    (tmp_path / "index.html").write_text("<p>original</p>", encoding="utf-8")
    _promote(tmp_path, "")
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<p>original</p>"


def test_missing_entry_is_loud(tmp_path: Path):
    (tmp_path / "index.html").write_text("<p>original</p>", encoding="utf-8")
    try:
        _promote(tmp_path, "nope.html")
    except AssertionError:
        return
    raise AssertionError("a missing entry page must raise, not silently no-op")


def test_promotion_matches_source():
    """The real exporter still promotes, and still raises on a missing page."""
    src = (REPO / "emptyos" / "sdk" / "exporter.py").read_text(encoding="utf-8")
    assert 'export_cfg.get("entry")' in src, "entry key no longer read"
    assert 'self.out_dir / "index.html"' in src, "promotion target changed"
    assert "does not exist" in src, "missing-entry error path removed"


# ── 2. single-html is a download ──────────────────────────────────────


def test_single_html_export_is_not_served_as_html():
    """text/html would route the bundle through the viewport middleware."""
    src = (REPO / "emptyos" / "web" / "server.py").read_text(encoding="utf-8")
    # Generous window: the branch carries a long explanatory comment before the
    # FileResponse, and a window that stops inside it fails for the wrong reason.
    block = re.search(r'if format == "single-html":(.{0,1600})', src, re.S)
    assert block, "single-html branch not found in the export route"
    body = block.group(1)
    assert 'media_type="application/octet-stream"' in body, (
        "single-html must be served as a download; text/html is rewritten by the "
        "viewport middleware and corrupts the inlined JS"
    )
    assert 'media_type="text/html"' not in body


def test_eos_js_still_contains_the_corrupting_substring():
    """Why the media type matters — if this ever stops being true, say so.

    The middleware keys on a literal ``<head>``. eos.js carries one inside a
    comment, so an inlined bundle is corruptible. This test is the reason the
    one above exists; if eos.js loses the substring, that test still stands but
    its rationale would need rewriting rather than quietly rotting.
    """
    js = (REPO / "emptyos" / "web" / "static" / "eos.js").read_text(encoding="utf-8")
    assert "<head>" in js, (
        "eos.js no longer contains a literal '<head>' — the corruption this "
        "guards against may no longer be reachable; re-check before relaxing "
        "the octet-stream requirement"
    )
