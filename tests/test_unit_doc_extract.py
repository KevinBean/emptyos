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
