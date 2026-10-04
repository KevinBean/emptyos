"""Unit tests for the OCR plugin (plugins/ocr/) — no daemon, no marker venv.

Covers the load-bearing contract from the plugin spec:
  - a BORN-DIGITAL pdf (has a text layer) must NOT trigger OCR — the provider
    raises `_NeedsNoOcr` so the read chain falls through to the page-marked
    extractor / filesystem provider (never shadowed).
  - a SCANNED pdf (no text layer) MUST route to OCR — the provider calls the
    plugin's `ocr()` (here a stub that records the call) instead of falling
    through.
  - `repaginate()` converts marker's 0-indexed `{N}----` separators to
    1-indexed `<!-- Page N of TOTAL -->` with the right page count.

Modules are loaded by path so the test needs neither a `plugins` package nor the
marker venv. `fitz` (PyMuPDF) builds the fixture PDFs in-process.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "ocr"


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, _PLUGIN_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load("ocr_runner_under_test", "runner.py")
try:
    plugin = _load("ocr_plugin_under_test", "plugin.py")
except Exception as exc:  # emptyos.sdk import failures shouldn't hide runner tests
    plugin = None
    _plugin_import_err = exc

fitz = pytest.importorskip("fitz", reason="PyMuPDF needed to build fixture PDFs")


# ── fixtures ────────────────────────────────────────────────────────────────

def _born_digital_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"Page {i + 1}. " + "The quick brown fox. " * 20)
    doc.save(str(path))
    doc.close()
    return path


def _scanned_pdf(path: Path, pages: int = 3) -> Path:
    """No text layer — blank pages stand in for image-only scans."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


class _StubPlugin:
    """Minimal stand-in for OcrPlugin — only the attrs/methods the provider reads."""

    def __init__(self, *, force=False):
        self.force = force
        self.probe_pages = 5
        self.min_chars_per_page = 50
        self.max_pages = 1200
        self.ocr_calls: list = []

    async def available(self) -> bool:
        return False

    async def ocr(self, path: str, *, page_count: int = 1, timeout=None) -> str:
        self.ocr_calls.append((path, page_count))
        raise RuntimeError("ocr venv unavailable (stub)")


def _provider(stub):
    assert plugin is not None, f"plugin module failed to import: {_plugin_import_err}"
    return plugin.OcrReadProvider(stub, base_path="")


# ── repaginate (pure) ───────────────────────────────────────────────────────

def test_repaginate_zero_indexed_to_one_indexed():
    raw = "first page text\n\n{1}" + "-" * 48 + "\n\nsecond page text"
    out = runner.repaginate(raw, total=2)
    assert "<!-- Page 1 of 2 -->" in out   # prepended (no {0} marker at top)
    assert "<!-- Page 2 of 2 -->" in out   # {1} -> page 2
    assert "{1}" not in out


def test_repaginate_leading_marker_not_duplicated():
    raw = "{0}" + "-" * 48 + "\n\nbody"
    out = runner.repaginate(raw, total=1)
    assert out.count("<!-- Page 1 of 1 -->") == 1


def test_repaginate_total_count_matches():
    raw = "a\n\n{1}" + "-" * 48 + "\n\nb\n\n{2}" + "-" * 48 + "\n\nc"
    out = runner.repaginate(raw, total=3)
    assert "<!-- Page 3 of 3 -->" in out
    assert "of 3 -->" in out and "of 2 -->" not in out


# ── pdf stats probe ─────────────────────────────────────────────────────────

def test_pdf_stats_born_digital_has_text(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    pdf = _born_digital_pdf(tmp_path / "born.pdf")
    n, has_text = plugin._pdf_stats(str(pdf), sample_pages=5, min_chars_per_page=50)
    assert n == 3
    assert has_text is True


def test_pdf_stats_scanned_no_text(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    pdf = _scanned_pdf(tmp_path / "scan.pdf")
    n, has_text = plugin._pdf_stats(str(pdf), sample_pages=5, min_chars_per_page=50)
    assert n == 3
    assert has_text is False


# ── provider routing ────────────────────────────────────────────────────────

def test_born_digital_does_not_trigger_ocr(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    pdf = _born_digital_pdf(tmp_path / "born.pdf")
    stub = _StubPlugin()
    prov = _provider(stub)
    with pytest.raises(plugin._NeedsNoOcr):
        asyncio.run(prov.execute(path=str(pdf)))
    assert stub.ocr_calls == []   # never routed to OCR


def test_scanned_routes_to_ocr(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    pdf = _scanned_pdf(tmp_path / "scan.pdf")
    stub = _StubPlugin()
    prov = _provider(stub)
    # stub.ocr raises (no venv) — the point is it was CALLED, not that it fell through.
    with pytest.raises(RuntimeError):
        asyncio.run(prov.execute(path=str(pdf)))
    assert len(stub.ocr_calls) == 1
    assert stub.ocr_calls[0][1] == 3   # page_count threaded through


def test_force_ocrs_born_digital(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    pdf = _born_digital_pdf(tmp_path / "born.pdf")
    stub = _StubPlugin(force=True)
    prov = _provider(stub)
    with pytest.raises(RuntimeError):
        asyncio.run(prov.execute(path=str(pdf)))
    assert len(stub.ocr_calls) == 1   # force skips the probe, OCRs anyway


def test_non_document_falls_through(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    md = tmp_path / "note.md"
    md.write_text("# hello", encoding="utf-8")
    stub = _StubPlugin()
    prov = _provider(stub)
    with pytest.raises(plugin._NeedsNoOcr):
        asyncio.run(prov.execute(path=str(md)))
    assert stub.ocr_calls == []


def test_max_pages_guard_blocks_huge_pdf(tmp_path):
    if plugin is None:
        pytest.skip(f"plugin import failed: {_plugin_import_err}")
    pdf = _scanned_pdf(tmp_path / "big.pdf", pages=4)
    stub = _StubPlugin()
    stub.max_pages = 2
    prov = _provider(stub)
    with pytest.raises(plugin._NeedsNoOcr):
        asyncio.run(prov.execute(path=str(pdf)))
    assert stub.ocr_calls == []   # guarded before spawning the venv
