"""BaseApp.report_response — the one response shape for an app report.

Daemon-free (so it can gate). Contract: `.claude/rules/app-reports.md`.

These pin the behaviours the nine hand-rolled report routes disagreed about
before the helper existed: which format a bare request gets, what an unknown
format does, and whether a failed render becomes a 500 or an in-band error.

`report_response` never touches `self`, so a bare sentinel stands in for the app
— constructing a real BaseApp would need a kernel and make this daemon-backed.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emptyos.sdk.base_app import BaseApp  # noqa: E402


class _Req:
    """Minimal stand-in for a Starlette request — only query_params is read."""

    def __init__(self, **params):
        self.query_params = params


def _call(**kwargs):
    return asyncio.run(BaseApp.report_response(object(), **kwargs))


# ── format negotiation ───────────────────────────────────────────────────────

def test_defaults_to_markdown_when_nothing_asks():
    res = _call(markdown_text="# Sheet\n\nbody", name="earthing-report")
    assert res.media_type == "text/markdown"
    assert b"# Sheet" in res.body


def test_query_param_selects_format():
    res = _call(markdown_text="x", name="r", request=_Req(format="md"))
    assert res.media_type == "text/markdown"


def test_markdown_is_inline_not_a_download():
    """REPORT-SPEC's contract is that the sheet renders in the browser, so the
    markdown face must not carry an attachment disposition."""
    res = _call(markdown_text="x", name="r")
    disposition = res.headers.get("content-disposition", "")
    assert "attachment" not in disposition


def test_markdown_alias_is_accepted():
    res = _call(markdown_text="x", name="r", request=_Req(format="markdown"))
    assert res.media_type == "text/markdown"


def test_fmt_argument_beats_the_query_param():
    """A caller that has already decided must win over the URL."""
    res = _call(markdown_text="x", name="r", fmt="md", request=_Req(format="pdf"))
    assert res.media_type == "text/markdown"


@pytest.mark.parametrize("bad", ["docx", "html", "json", "PDF."])
def test_unknown_format_is_an_in_band_error_not_an_exception(bad):
    res = _call(markdown_text="x", name="r", request=_Req(format=bad))
    assert isinstance(res, dict) and "unsupported report format" in res["error"]


def test_format_is_case_insensitive():
    res = _call(markdown_text="x", name="r", request=_Req(format="MD"))
    assert res.media_type == "text/markdown"


# ── name is a filename, so it is guarded ─────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "   ", "../etc/passwd", "a/b", "nul"])
def test_unsafe_report_name_is_refused_in_band(bad):
    res = _call(markdown_text="x", name=bad)
    assert isinstance(res, dict) and "invalid report name" in res["error"]


# ── pdf path (render is stubbed — Playwright is not a unit-test dependency) ──

def _stub_renderer(monkeypatch, fn):
    import emptyos.sdk.pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, "render_markdown_pdf", fn)


def test_pdf_renders_through_the_shared_renderer(monkeypatch, tmp_path):
    seen = {}

    def fake(md, out_path, style=None):
        seen["md"] = md
        seen["style"] = style
        Path(out_path).write_bytes(b"%PDF-1.4 stub")
        return Path(out_path)

    _stub_renderer(monkeypatch, fake)
    res = _call(markdown_text="# Sheet", name="earthing-report", fmt="pdf")

    assert seen["md"] == "# Sheet", "the app's markdown must reach the renderer verbatim"
    assert seen["style"] == "default"
    assert res.media_type == "application/pdf"
    assert res.filename == "earthing-report.pdf"


def test_pdf_style_is_forwarded(monkeypatch):
    seen = {}

    def fake(md, out_path, style=None):
        seen["style"] = style
        Path(out_path).write_bytes(b"%PDF")
        return Path(out_path)

    _stub_renderer(monkeypatch, fake)
    _call(markdown_text="x", name="r", fmt="pdf", style="slate")
    assert seen["style"] == "slate"


def test_failed_render_is_an_in_band_error_not_a_500(monkeypatch):
    def boom(md, out_path, style=None):
        raise RuntimeError("playwright missing")

    _stub_renderer(monkeypatch, boom)
    res = _call(markdown_text="x", name="r", fmt="pdf")
    assert isinstance(res, dict)
    assert "PDF render failed" in res["error"] and "playwright missing" in res["error"]


def test_pdf_is_not_written_into_the_vault(monkeypatch):
    """render_pdf resolves a relative path against the vault; a report is a
    transient download and must not land in the user's notes."""
    seen = {}

    def fake(md, out_path, style=None):
        seen["path"] = Path(out_path)
        Path(out_path).write_bytes(b"%PDF")
        return Path(out_path)

    _stub_renderer(monkeypatch, fake)
    _call(markdown_text="x", name="r", fmt="pdf")

    import tempfile
    assert seen["path"].is_absolute()
    assert seen["path"].parent == Path(tempfile.gettempdir())
