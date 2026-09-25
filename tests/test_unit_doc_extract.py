import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_doc_extract():
    spec = importlib.util.spec_from_file_location("doc_extract", ROOT / "scripts" / "doc_extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hn_prefixed_text_file_is_not_misdetected_as_caj(tmp_path):
    doc_extract = _load_doc_extract()
    path = tmp_path / "note.txt"
    path.write_text("HN plain text should stay text", encoding="utf-8")

    kind, text = doc_extract.extract(path)

    assert kind == "text"
    assert text == "HN plain text should stay text"


def test_caj_suffix_is_refused_even_without_magic_bytes(tmp_path):
    doc_extract = _load_doc_extract()
    path = tmp_path / "paper.caj"
    path.write_text("HN proprietary caj header variant", encoding="utf-8")

    with pytest.raises(SystemExit, match="CNKI \\.caj not supported"):
        doc_extract.extract(path)


# --- RTF \uN unicode-fallback handling -------------------------------------
# RTF writes a Unicode char as \uNNNN followed by an ANSI fallback byte for
# readers that predate Unicode. The fallback must be DISCARDED, not decoded --
# decoding it as gbk turns Western smart quotes into random CJK (mojibake).
B = bytes([92])  # one literal backslash


def _rtf(body: bytes) -> bytes:
    return b"{" + B + b"rtf1" + B + b"ansi " + body + b"}"


def test_rtf_unicode_fallback_escape_is_discarded_not_gbk_decoded():
    r"""\uNNNN followed by a \'xx fallback must yield only the Unicode char."""
    mod = _load_doc_extract()
    out = mod.from_rtf(_rtf(b"let" + B + b"u8217" + B + b"'92s pause"))
    assert "let\u2019s pause" in out, repr(out)
    assert "\u62af" not in out, f"gbk mojibake leaked: {out!r}"


def test_rtf_plain_fallback_char_still_discarded():
    r"""The pre-existing plain-char fallback path must keep working."""
    mod = _load_doc_extract()
    out = mod.from_rtf(_rtf(b"let" + B + b"u8217?s"))
    assert "let\u2019s" in out, repr(out)


def test_rtf_uc0_means_no_fallback_to_skip():
    r"""\uc0 declares zero fallback chars -- the next char is real content."""
    mod = _load_doc_extract()
    out = mod.from_rtf(_rtf(B + b"uc0 " + b"a" + B + b"u8217" + b"b"))
    assert "a\u2019b" in out, repr(out)


def test_rtf_real_gbk_bytes_still_decode_to_chinese():
    r"""Regression guard: genuine \'xx content (no \uN) still reads as gbk."""
    mod = _load_doc_extract()
    out = mod.from_rtf(_rtf(B + b"'d6" + B + b"'d0" + B + b"'ce" + B + b"'c4"))
    assert "\u4e2d\u6587" in out, repr(out)


def test_rtf_declared_ansicpg_overrides_gbk_default():
    r"""\ansicpg1252 means \'92 is a Western quote, not half a gbk char."""
    mod = _load_doc_extract()
    doc = b"{" + B + b"rtf1" + B + b"ansi" + B + b"ansicpg1252 " + b"it" + B + b"'92s}"
    out = mod.from_rtf(doc)
    assert "it\u2019s" in out, repr(out)


# --- anydoc fallback -------------------------------------------------------
# _ppt97_text falls back to anydoc when its record walk finds no text atoms.
# Two contracts on the helper itself; the one-line wiring is covered by the
# fact that a previously-failing deck now returns text.

def test_anydoc_text_returns_empty_when_library_absent(monkeypatch):
    r"""A fresh clone without anydoc must behave exactly as before."""
    mod = _load_doc_extract()
    import builtins
    real = builtins.__import__

    def _no_anydoc(name, *a, **k):
        if name == "anydoc":
            raise ImportError("no anydoc")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_anydoc)
    assert mod._anydoc_text(Path("whatever.ppt")) == ""


def test_anydoc_text_never_requests_hosted_ocr(monkeypatch):
    r"""`ocr=` ships pages to a third-party host, past the consent gate."""
    import sys as _sys
    mod = _load_doc_extract()
    calls = []

    class _Stub:
        def to_markdown(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "# stub"

    monkeypatch.setitem(_sys.modules, "anydoc", _Stub())
    out = mod._anydoc_text(Path("C:/tmp/deck.ppt"))
    assert out == "# stub"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert "ocr" not in kwargs, f"hosted OCR requested: {kwargs}"


def test_anydoc_text_swallows_a_conversion_failure(monkeypatch):
    r"""A NeedsOcr/parse error must degrade to '', not propagate."""
    import sys as _sys
    mod = _load_doc_extract()

    class _Boom:
        def to_markdown(self, *a, **k):
            raise ValueError("NeedsOcr")

    monkeypatch.setitem(_sys.modules, "anydoc", _Boom())
    assert mod._anydoc_text(Path("scan.ppt")) == ""
