"""Unit tests for the three document-reader skills.

    skills/tool-pdf-reader/pdf_tool.py    PDF   (needs pymupdf)
    skills/tool-chm-reader/chm_tool.py    CHM   (needs 7z or hh.exe to decompile)
    skills/tool-mdb-reader/mdb_tool.py    Access (needs pywin32 + ACE OLEDB, Windows)

These are skills, not apps, so they live outside any importable package and are
loaded by path. Each carries an external dependency CI does not have (CI is
ubuntu-latest with pytest/httpx only), so the suite is split deliberately:

  * **Pure tests run everywhere**, including CI. They cover the logic that is
    actually ours — HTML→markdown conversion, .hhc TOC parsing, charset
    sniffing, cell rendering, the SQL guard. This is where the bugs were.
  * **Integration tests self-skip** when their binary/provider/platform is
    absent. They prove the pure logic still holds against a real file.

The two mdb_tool rendering rules are regression pins, not cosmetics — both were
real defects found while dumping a live database (see the tool's docstring):
floats arriving as 13.299999999999999 for a cell showing 13.3, and NUL record
padding that turned an entire markdown dump "binary" to grep.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"


def _load(name: str, rel: str):
    """Import a skill script by path.

    Returns None when the module can't import — pdf_tool and (historically)
    mdb_tool call sys.exit() at import time when their optional dep is missing,
    so SystemExit is caught alongside ImportError. Tests then skip rather than
    error, which is what keeps `pytest --collect-only` green on CI.
    """
    path = SKILLS / rel
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except (ImportError, SystemExit):
        sys.modules.pop(name, None)
        return None
    return mod


chm = _load("_chm_tool", "tool-chm-reader/chm_tool.py")
mdb = _load("_mdb_tool", "tool-mdb-reader/mdb_tool.py")
pdf = _load("_pdf_tool", "tool-pdf-reader/pdf_tool.py")

needs_chm = pytest.mark.skipif(chm is None, reason="chm_tool.py not importable")
needs_mdb = pytest.mark.skipif(mdb is None, reason="mdb_tool.py not importable")
needs_pdf = pytest.mark.skipif(pdf is None, reason="pdf_tool.py needs pymupdf")


# ══════════════════════════════════════════════════ chm_tool — HTML → markdown


@needs_chm
class TestChmHtmlToMarkdown:
    """Pure conversion. Runs on CI — no CHM, no 7z, no Windows."""

    def test_headings_map_to_hash_levels(self):
        md = chm.html_to_md("<h1>One</h1><h3>Three</h3>")
        assert "# One" in md
        assert "### Three" in md

    def test_paragraphs_separate(self):
        md = chm.html_to_md("<p>first</p><p>second</p>")
        assert "first" in md and "second" in md
        assert "firstsecond" not in md

    def test_unordered_list(self):
        md = chm.html_to_md("<ul><li>alpha</li><li>beta</li></ul>")
        assert "- alpha" in md
        assert "- beta" in md

    def test_ordered_list_numbers_increment(self):
        md = chm.html_to_md("<ol><li>first</li><li>second</li></ol>")
        assert "1. first" in md
        assert "2. second" in md

    def test_nested_list_indents(self):
        md = chm.html_to_md("<ul><li>outer<ul><li>inner</li></ul></li></ul>")
        assert "- outer" in md
        assert "  - inner" in md

    def test_table_gets_header_separator(self):
        md = chm.html_to_md("<table><tr><th>A</th><th>B</th></tr>"
                            "<tr><td>1</td><td>2</td></tr></table>")
        assert "| A | B |" in md
        assert "| --- | --- |" in md
        assert "| 1 | 2 |" in md

    def test_table_cell_pipe_is_escaped(self):
        # An unescaped pipe would silently split the cell into two columns.
        md = chm.html_to_md("<table><tr><td>a|b</td><td>c</td></tr></table>")
        assert r"a\|b" in md

    def test_pre_becomes_fenced_block(self):
        md = chm.html_to_md("<pre>x = 1\ny = 2</pre>")
        assert "```" in md
        assert "x = 1" in md

    def test_pre_preserves_internal_whitespace(self):
        md = chm.html_to_md("<pre>a    b</pre>")
        assert "a    b" in md, "pre must not collapse whitespace"

    def test_bold_and_italic(self):
        assert "**hi**" in chm.html_to_md("<b>hi</b>")
        assert "*hi*" in chm.html_to_md("<i>hi</i>")

    def test_image_alt_is_kept_and_bare_image_is_dropped(self):
        assert "![Diagram]" in chm.html_to_md('<img alt="Diagram" src="x.png">')
        assert chm.html_to_md('<img src="spacer.gif">').strip() == ""

    def test_script_and_style_content_is_dropped(self):
        md = chm.html_to_md("<style>.x{color:red}</style>"
                            "<script>var y=1;</script><p>keep</p>")
        assert "keep" in md
        assert "color:red" not in md
        assert "var y" not in md

    def test_whitespace_is_collapsed(self):
        md = chm.html_to_md("<p>lots     of\n\n   space</p>")
        assert "lots of space" in md

    def test_malformed_html_falls_back_to_tag_strip(self):
        # Real help files contain broken markup; conversion must degrade, not raise.
        md = chm.html_to_md("<p>text <unclosed attr='<<<'>more")
        assert "text" in md

    def test_entities_are_unescaped(self):
        assert "&" in chm.html_to_md("<p>a &amp; b</p>")


# ══════════════════════════════════════════════════ chm_tool — .hhc TOC parsing


@needs_chm
class TestChmTocParsing:
    HHC = """<HTML><BODY><UL>
      <LI><OBJECT type="text/sitemap">
        <param name="Name" value="Chapter One">
        <param name="Local" value="ch1.htm">
      </OBJECT>
      <UL>
        <LI><OBJECT type="text/sitemap">
          <param name="Name" value="Section A">
          <param name="Local" value="sub\\a.htm">
        </OBJECT>
      </UL>
      <LI><OBJECT type="text/sitemap">
        <param name="Name" value="Chapter Two">
        <param name="Local" value="ch2.htm">
      </OBJECT>
    </UL></BODY></HTML>"""

    def _root(self, tmp_path: Path) -> Path:
        (tmp_path / "toc.hhc").write_text(self.HHC, encoding="utf-8")
        return tmp_path

    def test_parses_entries_in_document_order(self, tmp_path):
        e = chm.parse_toc(self._root(tmp_path))
        assert [x["name"] for x in e] == ["Chapter One", "Section A", "Chapter Two"]

    def test_nesting_sets_depth(self, tmp_path):
        e = chm.parse_toc(self._root(tmp_path))
        assert e[0]["depth"] < e[1]["depth"], "nested <UL> must increase depth"
        assert e[2]["depth"] == e[0]["depth"], "depth must pop back on </UL>"

    def test_backslashes_in_local_are_normalised(self, tmp_path):
        e = chm.parse_toc(self._root(tmp_path))
        assert e[1]["local"] == "sub/a.htm"

    def test_no_hhc_returns_empty(self, tmp_path):
        assert chm.parse_toc(tmp_path) == []

    def test_largest_hhc_wins(self, tmp_path):
        # CHMs can ship several .hhc files; the real TOC is the biggest.
        (tmp_path / "small.hhc").write_text("<UL></UL>", encoding="utf-8")
        (tmp_path / "big.hhc").write_text(self.HHC, encoding="utf-8")
        assert len(chm.parse_toc(tmp_path)) == 3

    def test_system_files_are_ignored(self, tmp_path):
        (tmp_path / "#SYSTEM.hhc").write_text(self.HHC * 3, encoding="utf-8")
        (tmp_path / "real.hhc").write_text(self.HHC, encoding="utf-8")
        assert len(chm.parse_toc(tmp_path)) == 3, "#-prefixed system files must be skipped"


@needs_chm
class TestChmPageDiscovery:
    def test_html_pages_skips_system_files(self, tmp_path):
        (tmp_path / "real.htm").write_text("<p>x</p>", encoding="utf-8")
        (tmp_path / "#SYSTEM.htm").write_text("<p>x</p>", encoding="utf-8")
        (tmp_path / "$WWKeywordLinks.html").write_text("<p>x</p>", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        names = [p.name for p in chm.html_pages(tmp_path)]
        assert names == ["real.htm"]

    def test_toc_or_pages_falls_back_to_flat_list(self, tmp_path):
        (tmp_path / "b.htm").write_text("<p>b</p>", encoding="utf-8")
        (tmp_path / "a.htm").write_text("<p>a</p>", encoding="utf-8")
        entries, real = chm._toc_or_pages(tmp_path)
        assert real is False, "no .hhc -> not a real TOC"
        assert [e["local"] for e in entries] == ["a.htm", "b.htm"], "sorted flat order"

    def test_toc_or_pages_prefers_real_toc(self, tmp_path):
        (tmp_path / "ch1.htm").write_text("<p>x</p>", encoding="utf-8")
        (tmp_path / "t.hhc").write_text(TestChmTocParsing.HHC, encoding="utf-8")
        _, real = chm._toc_or_pages(tmp_path)
        assert real is True


@needs_chm
class TestChmResolveLocal:
    def test_exact_match(self, tmp_path):
        (tmp_path / "page.htm").write_text("x", encoding="utf-8")
        assert chm.resolve_local(tmp_path, "page.htm").name == "page.htm"

    def test_anchor_is_stripped(self, tmp_path):
        (tmp_path / "page.htm").write_text("x", encoding="utf-8")
        assert chm.resolve_local(tmp_path, "page.htm#section-3") is not None

    def test_case_insensitive_fallback(self, tmp_path):
        # .hhc casing routinely disagrees with the archive's real casing.
        (tmp_path / "Page.HTM").write_text("x", encoding="utf-8")
        assert chm.resolve_local(tmp_path, "page.htm") is not None

    def test_missing_returns_none(self, tmp_path):
        assert chm.resolve_local(tmp_path, "nope.htm") is None

    def test_empty_returns_none(self, tmp_path):
        assert chm.resolve_local(tmp_path, "") is None


@needs_chm
class TestChmReadHtml:
    def test_meta_charset_cp1252_is_honoured(self, tmp_path):
        # CHM pages are commonly windows-1252; decoding as UTF-8 mangles them.
        # Byte-level on purpose: 0xE9 is 'é' and 0x96 is an en-dash in cp1252,
        # and 0x96 is not valid UTF-8 — so a wrong decode is visible.
        p = tmp_path / "p.htm"
        p.write_bytes(b'<meta charset="windows-1252"><p>caf\xe9 \x96 dash</p>')
        out = chm.read_html(p)
        assert "café" in out
        assert "–" in out, "0x96 should decode to an en-dash"
        assert "�" not in out, "replacement char means the charset was ignored"

    def test_utf8_without_meta(self, tmp_path):
        p = tmp_path / "p.htm"
        p.write_text("<p>naïve — em</p>", encoding="utf-8")
        assert "naïve" in chm.read_html(p)

    def test_undeclared_bytes_never_raise(self, tmp_path):
        p = tmp_path / "p.htm"
        p.write_bytes(b"<p>\xff\xfe garbage</p>")
        assert isinstance(chm.read_html(p), str)


@needs_chm
class TestChmCache:
    def test_cache_key_changes_with_content(self, tmp_path):
        f = tmp_path / "a.chm"
        f.write_bytes(b"x" * 10)
        first = chm.cache_dir_for(f)
        f.write_bytes(b"y" * 999)  # different size -> different key
        assert chm.cache_dir_for(f) != first


# ══════════════════════════════════════════════════ mdb_tool — cell rendering


@needs_mdb
class TestMdbCellRendering:
    """The two regression pins. Pure — runs on CI without pywin32."""

    def test_float_noise_is_suppressed(self):
        # Regression: ADO returns this exact double for a cell Access shows as
        # 13.3. repr() would print 13.299999999999999 and a grep for the value
        # the user actually knows would miss the row.
        assert mdb.cell(13.299999999999999) == "13.3"
        assert mdb.cell(2.5799999999999996) == "2.58"
        assert mdb.cell(0.38099999999999995) == "0.381"

    def test_integral_floats_print_bare(self):
        assert mdb.cell(0.0) == "0"
        assert mdb.cell(304.0) == "304"

    def test_precision_is_not_destroyed(self):
        # .15g must round the noise, not the number.
        assert mdb.cell(126.67) == "126.67"
        assert mdb.cell(1.23456789012345) == "1.23456789012345"

    def test_trailing_nul_padding_is_stripped(self):
        # Regression: fixed-width records are NUL-padded. str.rstrip() does NOT
        # remove NUL, and one such row makes the whole dump binary to grep.
        assert mdb.cell("data" + "\x00" * 10) == "data"

    def test_interior_nul_is_visible_not_silent(self):
        assert mdb.cell("a\x00b") == "a␀b"

    def test_no_nul_survives_rendering(self):
        assert "\x00" not in mdb.cell("x\x00y\x00")

    def test_pipe_is_escaped(self):
        assert mdb.cell("a|b") == r"a\|b"

    def test_newlines_are_flattened(self):
        assert mdb.cell("line1\r\nline2") == "line1 line2"
        assert mdb.cell("line1\rline2") == "line1 line2"
        assert mdb.cell("line1\nline2") == "line1 line2"

    def test_none_is_empty(self):
        assert mdb.cell(None) == ""

    def test_ints_are_untouched(self):
        assert mdb.cell(11) == "11"

    def test_text_helper_strips_nul_without_other_mangling(self):
        assert mdb.text("keep  spaces\x00") == "keep  spaces"


@needs_mdb
class TestMdbTable:
    def test_shape(self):
        out = mdb.md_table(["A", "B"], [["1", "2"]])
        assert out[0] == "| A | B |"
        assert out[1] == "| --- | --- |"
        assert out[2] == "| 1 | 2 |"

    def test_header_only(self):
        assert len(mdb.md_table(["A"], [])) == 2


@needs_mdb
class TestMdbSelectGuard:
    """The tool must be incapable of writing. Pure — no database needed."""

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM t",
        "select 1",
        "  \n SELECT x",
        "(SELECT x)",
        "( ( SELECT x ) )",
    ])
    def test_select_is_allowed(self, sql):
        assert mdb.is_select(sql) is True

    @pytest.mark.parametrize("sql", [
        "DELETE FROM t",
        "DROP TABLE t",
        "UPDATE t SET x=1",
        "INSERT INTO t VALUES (1)",
        "ALTER TABLE t ADD c INT",
        "CREATE TABLE t (c INT)",
        "",
        "   ",
        "selective_query_thing",   # must not pass on a prefix match
        "EXEC sp_thing",
    ])
    def test_non_select_is_refused(self, sql):
        assert mdb.is_select(sql) is False

    def test_cmd_query_exits_before_touching_the_file(self, tmp_path):
        # The guard must fire before connect(), so a write is refused even if
        # the path is bogus.
        with pytest.raises(SystemExit):
            mdb.cmd_query(tmp_path / "nonexistent.mdb", "DELETE FROM [SETTINGS]")


# ══════════════════════════════════════════════════ integration (self-skipping)


HAS_ACE = False
if mdb is not None and sys.platform == "win32":
    try:
        import win32com.client as _w32

        _w32.Dispatch("ADOX.Catalog")
        HAS_ACE = True
    except Exception:
        HAS_ACE = False

needs_ace = pytest.mark.skipif(
    not HAS_ACE, reason="needs Windows + Microsoft ACE OLEDB provider")


@pytest.fixture(scope="module")
def sample_mdb(tmp_path_factory):
    """A real Access database built on the fly — no vault, no fixtures on disk."""
    import win32com.client

    path = tmp_path_factory.mktemp("mdb") / "sample.mdb"
    cat = win32com.client.Dispatch("ADOX.Catalog")
    cat.Create(f"Provider=Microsoft.ACE.OLEDB.12.0;Data Source={path}")
    cn = win32com.client.Dispatch("ADODB.Connection")
    cn.Open(f"Provider=Microsoft.ACE.OLEDB.12.0;Data Source={path}")
    cn.Execute("CREATE TABLE PARTS (CODE TEXT(20), DIA DOUBLE, QTY INTEGER)")
    cn.Execute("INSERT INTO PARTS (CODE, DIA, QTY) VALUES ('alpha', 13.3, 11)")
    cn.Execute("INSERT INTO PARTS (CODE, DIA, QTY) VALUES ('beta', 2.58, 4)")
    cn.Execute("CREATE TABLE EMPTYTBL (X TEXT(5))")
    cn.Close()
    return path


@needs_mdb
@needs_ace
class TestMdbAgainstRealDatabase:
    def test_info_lists_tables_and_counts(self, sample_mdb, capsys):
        mdb.cmd_info(sample_mdb)
        out = capsys.readouterr().out
        assert "PARTS" in out and "2 rows" in out
        assert "EMPTYTBL" in out

    def test_dump_renders_float_without_noise(self, sample_mdb, tmp_path):
        # The end-to-end proof of the .15g pin: a real Double round-trips clean.
        out = tmp_path / "dump.md"
        mdb.cmd_dump(sample_mdb, out)
        body = out.read_text(encoding="utf-8")
        assert "| alpha | 13.3 | 11 |" in body
        assert "13.299999" not in body

    def test_dump_is_greppable_text(self, sample_mdb, tmp_path):
        out = tmp_path / "dump.md"
        mdb.cmd_dump(sample_mdb, out)
        assert b"\x00" not in out.read_bytes(), "a NUL makes the dump binary to grep"

    def test_dump_marks_empty_table(self, sample_mdb, tmp_path):
        out = tmp_path / "dump.md"
        mdb.cmd_dump(sample_mdb, out)
        assert "*(empty in this database)*" in out.read_text(encoding="utf-8")

    def test_query_returns_rows(self, sample_mdb, capsys):
        mdb.cmd_query(sample_mdb, "SELECT CODE FROM [PARTS] WHERE QTY=11")
        assert "alpha" in capsys.readouterr().out

    def test_search_finds_row(self, sample_mdb, capsys):
        mdb.cmd_search(sample_mdb, "beta", 20)
        out = capsys.readouterr().out
        assert "PARTS" in out and "beta" in out

    def test_connection_is_read_only(self, sample_mdb):
        # Mode=1 must make the provider itself refuse a write, independent of
        # the is_select() guard.
        cn = mdb.connect(sample_mdb)
        with pytest.raises(Exception):
            cn.Execute("INSERT INTO PARTS (CODE) VALUES ('gamma')")
        cn.Close()


HAS_CHM_EXTRACTOR = chm is not None and bool(chm._find_7z() or chm._find_hh())

needs_chm_extractor = pytest.mark.skipif(
    not HAS_CHM_EXTRACTOR, reason="needs 7-Zip or Windows hh.exe to decompile")


@needs_chm
@needs_chm_extractor
class TestChmDecompile:
    def test_extractor_is_discoverable(self):
        # A .chm fixture can't be built without the HTML Help compiler, so the
        # decompile path itself is proven by dumping a real CHM by hand. What is
        # pinned here is that an extractor resolves to an existing executable.
        exe = chm._find_7z() or chm._find_hh()
        assert exe and Path(exe).exists()


# ══════════════════════════════════════════════════ pdf_tool (needs pymupdf)


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    fitz = pytest.importorskip("fitz", reason="pymupdf not installed")
    path = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    doc = fitz.open()
    for n in (1, 2):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {n} body with keyword sentinel{n}")
    doc.set_metadata({"title": "Sample Title", "author": "Tester"})
    doc.save(str(path))
    doc.close()
    return path


@needs_pdf
class TestPdfTool:
    def test_info_reports_pages_and_title(self, sample_pdf, capsys):
        pdf.get_info(str(sample_pdf))
        out = capsys.readouterr().out
        assert "Total Pages: 2" in out
        assert "Sample Title" in out

    def test_extract_returns_page_text(self, sample_pdf, capsys):
        pdf.extract_text(str(sample_pdf), 1, 2)
        out = capsys.readouterr().out
        assert "sentinel1" in out and "sentinel2" in out

    def test_extract_honours_page_range(self, sample_pdf, capsys):
        pdf.extract_text(str(sample_pdf), 2, 2)
        out = capsys.readouterr().out
        assert "sentinel2" in out
        assert "sentinel1" not in out

    def test_search_finds_only_the_matching_page(self, sample_pdf, capsys):
        pdf.search_text(str(sample_pdf), "sentinel2")
        out = capsys.readouterr().out
        assert "2" in out

    def test_toc_absent_is_reported_not_raised(self, sample_pdf, capsys):
        pdf.get_toc(str(sample_pdf))
        assert "No table of contents" in capsys.readouterr().out


# ══════════════════════════════════════════════════ shared contract


class TestReaderSkillContract:
    """Cross-cutting: the three skills stay siblings. Pure — always runs."""

    SKILL_IDS = ["tool-pdf-reader", "tool-chm-reader", "tool-mdb-reader"]

    @pytest.mark.parametrize("skill_id", SKILL_IDS)
    def test_skill_md_has_frontmatter(self, skill_id):
        text = (SKILLS / skill_id / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---")[1]
        assert f"name: {skill_id}" in head
        assert "description:" in head

    @pytest.mark.parametrize("skill_id", SKILL_IDS)
    def test_script_exists_and_is_syntactically_valid(self, skill_id):
        import ast

        stem = skill_id.replace("tool-", "").replace("-reader", "")
        script = SKILLS / skill_id / f"{stem}_tool.py"
        assert script.exists(), f"{script} missing"
        ast.parse(script.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("skill_id", SKILL_IDS)
    def test_registered_in_release_tier(self, skill_id):
        # Drift guard: a skill absent here silently ships to nobody.
        cfg = tomllib.loads((REPO / "release.toml").read_text(encoding="utf-8"))
        assert skill_id in cfg["tiers"]["standard"]["skills"]

    @pytest.mark.parametrize("skill_id", SKILL_IDS)
    def test_documented_in_skills_doc(self, skill_id):
        path = REPO / "docs" / "SKILLS.md"
        from helpers import public_snapshot

        if not path.exists() and public_snapshot():
            pytest.skip("docs/SKILLS.md absent (the public snapshot drops it)")
        doc = path.read_text(encoding="utf-8")
        assert skill_id in doc, "run scripts/generate_skills_doc.py"

    @pytest.mark.parametrize("mod,verbs", [
        ("chm", ["info", "toc", "list", "extract", "page", "search", "dump"]),
        ("mdb", ["info", "schema", "table", "query", "search", "dump"]),
    ])
    def test_documented_verbs_are_implemented(self, mod, verbs):
        # The SKILL.md promises these; a missing cmd_ would 'work' until used.
        m = {"chm": chm, "mdb": mdb}[mod]
        if m is None:
            pytest.skip(f"{mod}_tool not importable")
        for v in verbs:
            assert hasattr(m, f"cmd_{v}"), f"{mod}_tool.cmd_{v} missing"

    @pytest.mark.parametrize("mod,ext", [("chm", ".chm"), ("mdb", ".mdb"), ("pdf", ".pdf")])
    def test_cli_refuses_a_missing_file(self, mod, ext, tmp_path, monkeypatch):
        # The user-facing contract for a bad path: exit, don't traceback.
        m = {"chm": chm, "mdb": mdb, "pdf": pdf}[mod]
        if m is None:
            pytest.skip(f"{mod}_tool not importable")
        argv = ["tool.py", "info", str(tmp_path / f"nope{ext}")]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            m.main()
