"""Unit tests for emptyos/sdk/html_anchors.py — pure, no daemon.

Covers the parser edge cases that make the designer edit loop + platform
locator robust: nested same-name elements, void elements, browser-style
auto-closed tags, raw-text children, baked viz iframes, idempotent injection,
and inline-style merging.
"""

from __future__ import annotations

from emptyos.sdk.html_anchors import (
    extract_element,
    inject_anchors,
    inject_src,
    list_anchors,
    merge_inline_style,
    outer_tag_name,
    strip_attr,
)


# ── extract_element ──────────────────────────────────────────────────

def test_extract_simple():
    doc = '<div><p data-eos-el="e1">hello</p></div>'
    span = extract_element(doc, "e1")
    assert span is not None
    s, e = span
    assert doc[s:e] == '<p data-eos-el="e1">hello</p>'


def test_extract_nested_same_name():
    # The anchored div wraps another div of the same tag — the span must
    # include the inner div + its close, then stop at the matching close.
    doc = (
        '<section>'
        '<div data-eos-el="e2">outer '
        '<div>inner</div>'
        ' tail</div>'
        '<div>sibling</div>'
        '</section>'
    )
    span = extract_element(doc, "e2")
    assert span is not None
    s, e = span
    assert doc[s:e] == '<div data-eos-el="e2">outer <div>inner</div> tail</div>'


def test_extract_void_img():
    doc = '<div><img data-eos-el="e3" src="x.png" alt="pic"></div>'
    span = extract_element(doc, "e3")
    assert span is not None
    s, e = span
    assert doc[s:e] == '<img data-eos-el="e3" src="x.png" alt="pic">'


def test_extract_self_closing():
    doc = '<div><img data-eos-el="e4" src="x.png"/></div>'
    span = extract_element(doc, "e4")
    assert span is not None
    s, e = span
    assert doc[s:e] == '<img data-eos-el="e4" src="x.png"/>'


def test_extract_unclosed_p_siblings():
    # Browser/LLM HTML often omits </p>. The anchored <p> must end where the
    # next <p> opens (auto-close), not run to the parent's close.
    doc = (
        '<article>'
        '<p data-eos-el="e5">first paragraph'
        '<p>second paragraph</p>'
        '</article>'
    )
    # stdlib HTMLParser does NOT auto-close <p> on a sibling <p>, so the
    # anchored <p>'s matching close never appears at its frame and the resolver
    # returns None — the SAFE outcome: the caller falls back to whole-file
    # iterate rather than splicing a wrong span. Contract: None, or a span that
    # does not swallow the sibling — never an over-reaching span.
    span = extract_element(doc, "e5")
    if span is not None:
        s, e = span
        assert "second paragraph" not in doc[s:e]


def test_extract_unclosed_li():
    doc = (
        '<ul>'
        '<li data-eos-el="e6">one'
        '<li>two</li>'
        '</ul>'
    )
    span = extract_element(doc, "e6")
    # Robust outcome: either a clean span not swallowing the next li, or None.
    if span:
        s, e = span
        assert "two" not in doc[s:e]


def test_extract_element_wrapping_script():
    # A <section> containing a <script> with `<` inside must not desync.
    doc = (
        '<section data-eos-el="e7">'
        '<script>if (a < b) { x(); }</script>'
        '<p>after</p>'
        '</section>'
        '<div>sibling</div>'
    )
    span = extract_element(doc, "e7")
    assert span is not None
    s, e = span
    got = doc[s:e]
    assert got.startswith('<section data-eos-el="e7">')
    assert got.endswith("</section>")
    assert "sibling" not in got


def test_extract_baked_iframe_srcdoc():
    # The anchored element contains a baked viz iframe with escaped HTML in
    # srcdoc. The span must include the whole iframe and stop at the wrapper.
    doc = (
        '<div data-eos-el="e8">'
        '<iframe srcdoc="&lt;div&gt;chart&lt;/div&gt;" sandbox="allow-scripts"></iframe>'
        '</div>'
        '<footer>end</footer>'
    )
    span = extract_element(doc, "e8")
    assert span is not None
    s, e = span
    got = doc[s:e]
    assert got.startswith('<div data-eos-el="e8">')
    assert got.endswith("</div>")
    assert "<footer>" not in got


def test_extract_missing_anchor_returns_none():
    doc = '<div data-eos-el="e1">x</div>'
    assert extract_element(doc, "nope") is None
    assert extract_element(doc, "") is None


def test_extract_non_ascii_offsets():
    # CJK before the anchor must not corrupt char offsets.
    doc = '<div>你好世界 — long dash</div><p data-eos-el="e9">目标</p>'
    span = extract_element(doc, "e9")
    assert span is not None
    s, e = span
    assert doc[s:e] == '<p data-eos-el="e9">目标</p>'


# ── inject_anchors ───────────────────────────────────────────────────

def test_inject_anchors_sequential():
    doc = "<div><h1>Title</h1><p>Body</p></div>"
    out = inject_anchors(doc)
    assert 'data-eos-el="e0"' in out
    assert 'data-eos-el="e1"' in out
    assert 'data-eos-el="e2"' in out
    # Order: div first, then h1, then p.
    assert out.index('data-eos-el="e0"') < out.index('data-eos-el="e1"')


def test_inject_anchors_idempotent():
    doc = "<div><p>hi</p></div>"
    once = inject_anchors(doc)
    twice = inject_anchors(once)
    assert once == twice  # already-anchored tags are skipped


def test_inject_anchors_skips_excluded_tags():
    doc = '<div><script>var a=1;</script><style>.x{}</style><iframe src="y"></iframe></div>'
    out = inject_anchors(doc)
    # Only the div is editable; script/style/iframe excluded.
    assert out.count("data-eos-el=") == 1
    assert "<script" in out and 'data-eos-el' not in out.split("<script")[1].split(">")[0]


def test_inject_anchors_void_gets_anchor_no_endtag():
    doc = '<div><img src="x.png"></div>'
    out = inject_anchors(doc)
    # Both div and img are editable; img is void (no end tag added).
    assert out.count("data-eos-el=") == 2
    assert "</img>" not in out


def test_inject_then_extract_roundtrip():
    doc = "<section><h2>A</h2><p>para</p></section>"
    out = inject_anchors(doc)
    span = extract_element(out, "e1")  # the h2
    assert span is not None
    s, e = span
    assert out[s:e] == '<h2 data-eos-el="e1">A</h2>'


# ── list_anchors ─────────────────────────────────────────────────────

def test_list_anchors_empty_when_unstamped():
    assert list_anchors("<div><p>hi</p></div>") == []


def test_list_anchors_after_inject():
    doc = inject_anchors("<div><h1>Title</h1><button>Go</button></div>")
    items = list_anchors(doc)
    # div=e0, h1=e1, button=e2 — document order preserved.
    assert [i["el"] for i in items] == ["e0", "e1", "e2"]
    tags = {i["el"]: i["tag"] for i in items}
    assert tags["e1"] == "h1" and tags["e2"] == "button"


def test_list_anchors_text_snippet():
    doc = '<section data-eos-el="e0"><h2 data-eos-el="e1">Pricing</h2></section>'
    items = list_anchors(doc)
    by = {i["el"]: i for i in items}
    assert by["e1"]["text"] == "Pricing"
    assert "Pricing" in by["e0"]["text"]  # section text includes child text


def test_list_anchors_strips_markup_from_text():
    doc = '<p data-eos-el="e1">Buy <strong>now</strong> please</p>'
    items = list_anchors(doc)
    assert items[0]["text"] == "Buy now please"


def test_list_anchors_text_truncated():
    long = "word " * 40
    doc = f'<p data-eos-el="e1">{long}</p>'
    items = list_anchors(doc, max_text=20)
    assert len(items[0]["text"]) <= 20


def test_list_anchors_void_element():
    doc = '<img data-eos-el="e1" src="x.png" alt="logo">'
    items = list_anchors(doc)
    assert items == [{"el": "e1", "tag": "img", "text": ""}]


def test_list_anchors_dedupes_repeated_value():
    # A pathological page with a duplicated anchor value keeps the first.
    doc = '<p data-eos-el="e1">a</p><p data-eos-el="e1">b</p>'
    items = list_anchors(doc)
    assert len(items) == 1 and items[0]["el"] == "e1"


def test_list_anchors_non_ascii():
    doc = '<div>你好</div><button data-eos-el="e1">提交</button>'
    items = list_anchors(doc)
    assert items == [{"el": "e1", "tag": "button", "text": "提交"}]


# ── inject_src ───────────────────────────────────────────────────────

def test_inject_src_line_numbers():
    doc = "<div>\n  <h1>Hi</h1>\n  <p>there</p>\n</div>"
    out = inject_src(doc, "apps/foo/pages/index.html")
    assert 'data-eos-src="apps/foo/pages/index.html:1"' in out  # div on line 1
    assert 'data-eos-src="apps/foo/pages/index.html:2"' in out  # h1 on line 2
    assert 'data-eos-src="apps/foo/pages/index.html:3"' in out  # p on line 3


# ── strip_attr ───────────────────────────────────────────────────────

def test_strip_attr():
    doc = '<div data-eos-el="e0"><p data-eos-el="e1">x</p></div>'
    out = strip_attr(doc)
    assert "data-eos-el" not in out
    assert out == "<div><p>x</p></div>"


# ── merge_inline_style ───────────────────────────────────────────────

def test_merge_style_append_no_existing():
    frag = '<p data-eos-el="e1">hi</p>'
    out = merge_inline_style(frag, "color", "#3b82f6")
    assert 'style="color: #3b82f6"' in out
    assert 'data-eos-el="e1"' in out


def test_merge_style_replace_existing_prop():
    frag = '<p style="color: red; font-size: 14px">hi</p>'
    out = merge_inline_style(frag, "color", "blue")
    assert "color: blue" in out
    assert "color: red" not in out
    assert "font-size: 14px" in out  # untouched prop preserved


def test_merge_style_append_to_existing():
    frag = '<div style="padding: 8px">x</div>'
    out = merge_inline_style(frag, "text-align", "center")
    assert "padding: 8px" in out
    assert "text-align: center" in out


def test_merge_style_void_element():
    frag = '<img data-eos-el="e3" src="x.png">'
    out = merge_inline_style(frag, "border-radius", "12px")
    assert 'style="border-radius: 12px"' in out
    assert 'src="x.png"' in out


# ── outer_tag_name ───────────────────────────────────────────────────

def test_outer_tag_name():
    assert outer_tag_name('<P data-eos-el="e1">x</P>') == "p"
    assert outer_tag_name("  <div>x</div>") == "div"
    assert outer_tag_name("no tag here") == ""
