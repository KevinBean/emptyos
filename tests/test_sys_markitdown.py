"""Smoke test for the MarkItDown read provider (plugins/markitdown).

Self-contained end-to-end: builds a real PPTX + XLSX fixture with the daemon
interpreter (python-pptx / openpyxl are already daemon deps), then runs each
through ``MarkItDownReadProvider``, which subprocesses to the isolated user-home
venv and returns markdown.

It does NOT use the daemon's HTTP surface, but per the repo's conftest
convention a ``test_sys_*`` module is gated on the daemon being up, so it runs
in a normal session and skips cleanly when the venv isn't installed.

Pins the two scoping guarantees from the plugin contract:
  - PDF is NOT converted (falls through to the filesystem read provider).
  - Plain markdown (.md) is NOT converted (same fall-through).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from plugins.markitdown.plugin import (  # noqa: E402
    SUPPORTED_EXTENSIONS,
    MarkItDownPlugin,
    MarkItDownReadProvider,
    _default_python_exe,
    _UnsupportedFormat,
)


def _venv_python() -> str:
    """Resolve the venv interpreter the same way the plugin does: an
    ``[plugins.markitdown] python_exe`` override in emptyos.toml, else the
    canonical user-home default."""
    cfg = REPO / "emptyos.toml"
    if cfg.exists():
        try:
            import tomllib

            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
            override = data.get("plugins", {}).get("markitdown", {}).get("python_exe")
            if override:
                return override
        except Exception:
            pass
    return _default_python_exe()


@pytest.fixture(scope="module")
def provider():
    """Real plugin (no kernel) + real provider, pointed at the venv."""
    py = _venv_python()
    if not Path(py).exists():
        pytest.skip(f"markitdown venv not installed at {py}")
    plug = MarkItDownPlugin(kernel=None, manifest={})
    plug._python_exe = py
    plug._runner = str(REPO / "plugins" / "markitdown" / "runner.py")
    asyncio.run(plug._probe_launch())
    if not asyncio.run(plug.available()):
        pytest.skip(f"markitdown venv present but not launchable: {plug._launch_err}")
    return MarkItDownReadProvider(plug, base_path="")


@pytest.fixture(scope="module")
def pptx_file(tmp_path_factory):
    pptx = pytest.importorskip("pptx")  # python-pptx
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "MarkItDown Smoke"
    slide.placeholders[1].text = "Hello from a slide"
    out = tmp_path_factory.mktemp("mid") / "deck.pptx"
    prs.save(str(out))
    return out


@pytest.fixture(scope="module")
def xlsx_file(tmp_path_factory):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Name", "Score"])
    ws.append(["Alice", 91])
    ws.append(["Bob", 84])
    out = tmp_path_factory.mktemp("mid") / "book.xlsx"
    wb.save(str(out))
    return out


def test_pptx_conversion(provider, pptx_file):
    md = asyncio.run(provider.execute(path=str(pptx_file)))
    assert "MarkItDown Smoke" in md
    assert "Hello from a slide" in md
    # MarkItDown emits a slide-number marker — confirms PPTX structure decoded.
    assert "Slide number" in md


def test_xlsx_conversion(provider, xlsx_file):
    md = asyncio.run(provider.execute(path=str(xlsx_file)))
    # XLSX renders as a markdown table.
    assert "| Name | Score |" in md
    assert "Alice" in md and "Bob" in md
    assert "| --- |" in md  # the table header separator row


def test_pdf_is_not_converted(provider):
    # PDF must fall through to the filesystem provider — the KB page-marked
    # extractor owns PDFs. The suffix check runs before any file access, so a
    # non-existent path still exercises the routing decision.
    with pytest.raises(_UnsupportedFormat):
        asyncio.run(provider.execute(path="some/report.pdf"))


def test_markdown_falls_through(provider):
    with pytest.raises(_UnsupportedFormat):
        asyncio.run(provider.execute(path="notes/journal.md"))


def test_supported_set_scoping():
    # Contract guards independent of the venv being installed.
    assert ".pdf" not in SUPPORTED_EXTENSIONS
    assert ".md" not in SUPPORTED_EXTENSIONS
    for ext in (".pptx", ".xlsx", ".docx", ".epub", ".html", ".csv", ".json", ".xml", ".zip"):
        assert ext in SUPPORTED_EXTENSIONS
