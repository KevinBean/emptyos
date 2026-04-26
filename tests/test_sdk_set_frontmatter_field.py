"""Tests for emptyos.sdk.utils.set_frontmatter_field."""

from emptyos.sdk.utils import set_frontmatter_field, parse_frontmatter


def test_replace_existing_key():
    content = "---\ntitle: old\nstatus: draft\n---\nbody\n"
    result = set_frontmatter_field(content, "status", "published")
    assert "status: published" in result
    assert "status: draft" not in result
    assert "title: old" in result
    assert result.endswith("body\n")


def test_insert_new_key():
    content = "---\ntitle: hi\n---\nbody\n"
    result = set_frontmatter_field(content, "status", "draft")
    fm = parse_frontmatter(result)
    assert fm["title"] == "hi"
    assert fm["status"] == "draft"
    assert "body" in result


def test_no_frontmatter_block_creates_one():
    content = "just body text\n"
    result = set_frontmatter_field(content, "title", "hello")
    assert result.startswith("---\ntitle: hello\n---\n")
    assert result.endswith("just body text\n")


def test_empty_content():
    result = set_frontmatter_field("", "k", "v")
    assert result == "---\nk: v\n---\n"


def test_list_value_caller_encodes():
    content = "---\ntags: [a]\n---\n"
    result = set_frontmatter_field(content, "tags", "[a, b, c]")
    assert "tags: [a, b, c]" in result


def test_quoted_value_caller_encodes():
    content = "---\nauthor: x\n---\n"
    result = set_frontmatter_field(content, "author", '"Jane Doe"')
    assert 'author: "Jane Doe"' in result


def test_unterminated_frontmatter_falls_through_to_prepend():
    content = "---\nbroken\n"
    result = set_frontmatter_field(content, "k", "v")
    assert result.startswith("---\nk: v\n---\n")


def test_preserves_body_unchanged():
    body = "# heading\n\n- list item\n- another\n\nparagraph."
    content = f"---\nk: 1\n---\n{body}"
    result = set_frontmatter_field(content, "k", "2")
    assert result.endswith(body)
