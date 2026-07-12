"""Unit tests for emptyos.sdk.viz_embed — pure, no daemon, no kernel.

Run: python -m pytest tests/test_unit_viz_embed.py -v
"""

from __future__ import annotations

from emptyos.sdk.viz_embed import (
    embed_marker,
    fallback_block,
    id_from_artifact_path,
    parse_embed_markers,
    parse_html_attrs,
    px_height,
    srcdoc_iframe,
)


class TestIdFromArtifactPath:
    def test_standard_path(self):
        assert id_from_artifact_path("30_Resources/EmptyOS/viz/outputs/abc123/scene.html") == "abc123"

    def test_windows_separators(self):
        assert id_from_artifact_path("30_Resources\\EmptyOS\\viz\\outputs\\def456\\scene.html") == "def456"

    def test_trailing_slash(self):
        assert id_from_artifact_path("foo/outputs/xyz/scene.html/") == "xyz"

    def test_empty(self):
        assert id_from_artifact_path("") == ""

    def test_too_short(self):
        assert id_from_artifact_path("scene.html") == ""  # len<2 → no id


class TestPxHeight:
    def test_bare_int(self):
        assert px_height("360") == "360px"

    def test_int_value(self):
        assert px_height(500) == "500px"

    def test_already_px(self):
        assert px_height("360px") == "360px"

    def test_other_units(self):
        assert px_height("80vh") == "80vh"
        assert px_height("50%") == "50%"

    def test_empty_uses_default(self):
        assert px_height("") == "360px"
        assert px_height(None) == "360px"
        assert px_height("", default=200) == "200px"

    def test_garbage_uses_default(self):
        assert px_height("not-a-height") == "360px"


class TestParseHtmlAttrs:
    def test_double_quoted(self):
        assert parse_html_attrs('data-viz="chart" data-height="360"') == {
            "data-viz": "chart", "data-height": "360"}

    def test_single_quoted(self):
        assert parse_html_attrs("data-viz='mermaid'") == {"data-viz": "mermaid"}

    def test_lowercased_keys(self):
        assert parse_html_attrs('DATA-VIZ="x"') == {"data-viz": "x"}

    def test_empty(self):
        assert parse_html_attrs("") == {}


class TestSrcdocIframe:
    def test_basic_shape(self):
        out = srcdoc_iframe("<h1>hi</h1>", height="200px", title="T")
        assert out.startswith("<iframe srcdoc=")
        assert 'sandbox="allow-scripts"' in out
        assert "height:200px" in out
        assert 'title="T"' in out

    def test_never_same_origin_by_default(self):
        out = srcdoc_iframe("<p>x</p>")
        assert "allow-same-origin" not in out

    def test_quote_breakout_neutralized(self):
        # A literal " in the content must not close the srcdoc attribute.
        out = srcdoc_iframe('<img alt="">"><script>alert(1)</script>')
        assert "&quot;" in out
        # The raw closing-attr sequence must not survive verbatim.
        assert '"><script>alert(1)</script>' not in out

    def test_iframe_breakout_neutralized(self):
        # A literal </iframe> in content must not close the frame early.
        out = srcdoc_iframe("before</iframe>after")
        assert "&lt;/iframe&gt;" in out
        # Exactly one real closing tag (ours), none injected by content.
        assert out.count("</iframe>") == 1

    def test_height_normalized(self):
        assert "height:360px" in srcdoc_iframe("<p>x</p>", height="360")

    def test_empty_content(self):
        out = srcdoc_iframe("")
        assert 'srcdoc=""' in out


class TestFallbackBlock:
    def test_contains_shape_and_brief(self):
        out = fallback_block("chart", "monthly revenue", "viz unavailable")
        assert "chart" in out
        assert "monthly revenue" in out
        assert "viz unavailable" in out

    def test_escapes_content(self):
        out = fallback_block("<x>", "<y>", "<z>")
        assert "<x>" not in out
        assert "&lt;x&gt;" in out


class TestEmbedMarker:
    def test_build(self):
        m = embed_marker("abc123", mode="snapshot", shape="mermaid")
        assert m == "<!-- eos:viz-embed embed_id=abc123 mode=snapshot shape=mermaid -->"

    def test_roundtrip_single(self):
        body = "intro\n\n" + embed_marker("abc123", mode="reference", shape="chart") + "\n\noutro"
        parsed = parse_embed_markers(body)
        assert len(parsed) == 1
        assert parsed[0]["embed_id"] == "abc123"
        assert parsed[0]["mode"] == "reference"
        assert parsed[0]["shape"] == "chart"
        assert parsed[0]["raw"] in body

    def test_roundtrip_multiple_in_order(self):
        body = (
            embed_marker("aaa", shape="mermaid") + "\nmiddle\n"
            + embed_marker("bbb", mode="reference", shape="3d-scene")
        )
        parsed = parse_embed_markers(body)
        assert [p["embed_id"] for p in parsed] == ["aaa", "bbb"]
        assert [p["mode"] for p in parsed] == ["snapshot", "reference"]

    def test_no_markers(self):
        assert parse_embed_markers("just prose, no markers") == []

    def test_marker_missing_id_skipped(self):
        assert parse_embed_markers("<!-- eos:viz-embed mode=snapshot -->") == []
