"""Unit tests: apps/public/standard/library/shared.py — pure functions, no daemon."""

from apps.library.shared import (
    citekey_for,
    find_doi,
    normalize_str_list,
    to_bibtex_entry,
    to_csl_json,
)


class TestFindDoi:
    def test_finds_doi_in_text(self):
        assert find_doi("See doi:10.1109/TPWRS.2019.1234567 for details.") == "10.1109/TPWRS.2019.1234567"

    def test_strips_trailing_punctuation(self):
        assert find_doi("(https://doi.org/10.1145/3442188.3445922).") == "10.1145/3442188.3445922"

    def test_no_doi_returns_empty(self):
        assert find_doi("no identifier here") == ""

    def test_empty_input(self):
        assert find_doi("") == ""
        assert find_doi(None) == ""


class TestNormalizeStrList:
    def test_from_list(self):
        assert normalize_str_list(["a", " b ", ""]) == ["a", "b"]

    def test_from_comma_string(self):
        assert normalize_str_list("a, b,  c") == ["a", "b", "c"]

    def test_from_none(self):
        assert normalize_str_list(None) == []


class TestCitekeyFor:
    def test_basic_shape(self):
        ck = citekey_for(["Ada Lovelace"], "2024", "The Analytical Engine")
        assert ck == "lovelace2024analytical"

    def test_skips_stopwords(self):
        ck = citekey_for(["Alan Turing"], "1950", "On Computing Machinery")
        assert ck.startswith("turing1950")
        assert "computing" in ck  # "on" (stopword) skipped

    def test_no_authors_falls_back_to_anon(self):
        ck = citekey_for([], "2020", "Untitled Work")
        assert ck.startswith("anon2020")

    def test_no_year_falls_back_to_nd(self):
        ck = citekey_for(["Smith"], "", "A Study")
        assert ck.startswith("smithnd")

    def test_collision_appends_suffix(self):
        base = citekey_for(["Smith"], "2020", "Power Systems")
        taken = {base}
        second = citekey_for(["Smith"], "2020", "Power Systems", taken=taken)
        assert second != base
        assert second.startswith(base)


class TestBibtexExport:
    def test_article_type_when_journal_present(self):
        entry = to_bibtex_entry({
            "citekey": "smith2020test", "title": "A Test Paper", "authors": ["Jane Smith"],
            "year": "2020", "journal": "Test Journal", "doi": "10.1/x",
        })
        assert entry.startswith("@article{smith2020test,")
        assert "author = {Jane Smith}," in entry
        assert "journal = {Test Journal}," in entry

    def test_misc_type_without_journal(self):
        entry = to_bibtex_entry({"citekey": "x2020", "title": "T", "authors": []})
        assert entry.startswith("@misc{x2020,")

    def test_escapes_braces(self):
        entry = to_bibtex_entry({"citekey": "k", "title": "A {weird} Title"})
        assert r"A \{weird\} Title" in entry


class TestCslJsonExport:
    def test_shape(self):
        item = to_csl_json({
            "citekey": "smith2020test", "title": "A Test Paper", "authors": ["Jane Smith"],
            "year": "2020", "journal": "Test Journal", "doi": "10.1/x",
        })
        assert item["id"] == "smith2020test"
        assert item["type"] == "article-journal"
        assert item["author"] == [{"given": "Jane", "family": "Smith"}]
        assert item["issued"] == {"date-parts": [[2020]]}
        assert item["DOI"] == "10.1/x"

    def test_webpage_type_without_journal(self):
        item = to_csl_json({"citekey": "k", "title": "T"})
        assert item["type"] == "webpage"
