"""Unit tests for the landing-page parser (apps/publish/builder.py).

No daemon, no playwright — pure parse-and-assert. Covers the slots that
shape what visitors see on a project-site landing page:
hero/tagline/CTA, hero-note callout, feature cards (with leading-emoji
icon split), reserved Screenshots/Gallery section, metric strip from
`<!-- metrics: ... -->`, and blog-preview detection.
"""

import pytest

from apps.publish.builder import _parse_landing_sections


def parse(md: str) -> dict:
    return _parse_landing_sections(md)


def test_tagline_and_cta_split_from_hero():
    md = (
        "A short tagline paragraph.\n\n"
        "[Try it](https://example.com)\n"
        "[Docs](docs.html)\n"
    )
    p = parse(md)
    assert p["tagline"] == "A short tagline paragraph."
    assert p["cta_links"] == [("Try it", "https://example.com"), ("Docs", "docs.html")]
    assert p["hero_note_md"] == ""


def test_hero_note_captures_blockquote_after_first_paragraph():
    md = (
        "Tagline line.\n\n"
        "[Demo](demo.html)\n\n"
        "> Heads up — this is a sandbox.\n"
    )
    p = parse(md)
    assert p["tagline"] == "Tagline line."
    assert p["hero_note_md"].startswith("> Heads up")


def test_feature_icons_split_from_heading():
    md = (
        "Tagline.\n\n"
        "## 🧠 Ask AI anywhere\nbody one\n\n"
        "## 📂 Your notes stay yours\nbody two\n\n"
        "## Plain heading\nbody three\n"
    )
    p = parse(md)
    by_heading = {f["heading"]: f["icon"] for f in p["features"]}
    assert by_heading["Ask AI anywhere"] == "🧠"
    assert by_heading["Your notes stay yours"] == "📂"
    assert by_heading["Plain heading"] == ""


def test_metric_strip_parses_value_and_label():
    md = (
        "Tagline.\n\n"
        "<!-- metrics: 44 Apps · 91% Integrity · 9 Capabilities -->\n"
    )
    p = parse(md)
    assert p["metrics"] == [
        {"value": "44", "label": "Apps"},
        {"value": "91%", "label": "Integrity"},
        {"value": "9", "label": "Capabilities"},
    ]


def test_metric_strip_absent_yields_empty_list():
    p = parse("Tagline only.\n")
    assert p["metrics"] == []


def test_gallery_section_extracts_images_and_alts():
    md = (
        "Tagline.\n\n"
        "## Screenshots\n\n"
        "![Topology graph](topo.png)\n"
        "![[home.png]]\n"
        "Some prose between.\n"
        "![Journal](sub/journal.jpg)\n"
    )
    p = parse(md)
    srcs = [(g["src"], g["alt"]) for g in p["gallery_items"]]
    assert srcs == [
        ("topo.png", "Topology graph"),
        ("home.png", ""),
        ("sub/journal.jpg", "Journal"),
    ]
    assert p["gallery_heading"] == "Screenshots"
    # Reserved section must NOT also appear as a feature card.
    assert all(f["heading"].lower() != "screenshots" for f in p["features"])


def test_gallery_aliases():
    for heading in ("Gallery", "Images", "Tour"):
        md = f"Tagline.\n\n## {heading}\n\n![one](a.png)\n"
        p = parse(md)
        assert len(p["gallery_items"]) == 1
        assert all(f["heading"].lower() != heading.lower() for f in p["features"])


def test_blog_preview_detected_and_section_filtered():
    md = (
        "Tagline.\n\n"
        "## Blog\nlatest stuff\n\n"
        "## Real Feature\nbody\n"
    )
    p = parse(md)
    assert p["has_blog_preview"] is True
    headings = [f["heading"] for f in p["features"]]
    assert "Real Feature" in headings
    assert "Blog" not in headings
