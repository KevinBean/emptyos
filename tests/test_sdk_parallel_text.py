"""Unit tests for emptyos.sdk.parallel_text — the parallel-text parser behind
EOS_UI.parallelText. Pure functions; no daemon required (CI-safe)."""
import pytest

from emptyos.sdk.parallel_text import parse_parallel_md, section_label


def test_basic_title_section_rows():
    md = """# Doc Title

## 启请偈

| 原文 | 注音 |
|---|---|
| 妙湛總持不動尊 | miào zhàn |
| 首楞嚴王世稀有 | shǒu léng |
"""
    d = parse_parallel_md(md)
    assert d["title"] == "Doc Title"
    assert len(d["sections"]) == 1
    sec = d["sections"][0]
    assert sec["title"] == "启请偈"
    assert sec["rows"] == [
        {"left": "妙湛總持不動尊", "right": "miào zhàn"},
        {"left": "首楞嚴王世稀有", "right": "shǒu léng"},
    ]


def test_header_and_separator_rows_skipped():
    md = """## S
| 原文 | 注音 |
|------|------|
| a | b |
"""
    rows = parse_parallel_md(md)["sections"][0]["rows"]
    assert rows == [{"left": "a", "right": "b"}]


def test_fenced_blocks_captured():
    md = """## 咒心
```
哆姪他 唵
```
```
duo zhi tuo / an
```
"""
    sec = parse_parallel_md(md)["sections"][0]
    assert sec["rows"] == []
    assert sec["blocks"] == ["哆姪他 唵", "duo zhi tuo / an"]


def test_empty_section_dropped():
    md = """## Empty

just prose, no table, no fence

## Real
| x | y |
|---|---|
| 1 | 2 |
"""
    d = parse_parallel_md(md)
    titles = [s["title"] for s in d["sections"]]
    assert titles == ["Real"]


def test_multiple_sections_and_first_h1_wins():
    md = """# First
# Second
## A
| a | b |
| c | d |
## B
| e | f |
"""
    d = parse_parallel_md(md)
    assert d["title"] == "First"
    assert [s["title"] for s in d["sections"]] == ["A", "B"]
    assert len(d["sections"][0]["rows"]) == 2
    assert d["sections"][1]["rows"] == [{"left": "e", "right": "f"}]


def test_empty_and_none_input():
    assert parse_parallel_md("") == {"title": "", "sections": []}
    assert parse_parallel_md(None) == {"title": "", "sections": []}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("第一會【待校】", "第一會"),
        ("启请偈（确定 · 普通话）", "启请偈"),
        ("Section (draft)", "Section"),
        ("纯标题", "纯标题"),
    ],
)
def test_section_label_strips_annotations(raw, expected):
    assert section_label(raw) == expected
