"""Images survive the OpenAI-compat normalizer (desktop GUI plan, B3).

``_normalize_messages_for_openai`` flattened every content-part list to its
text blocks, so an attached screenshot never reached the model: the request
went out as text alone and the model answered as if nothing were attached.
These pin the multimodal shape through the real normalizer.
"""

from __future__ import annotations

from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider

DATA = "data:image/png;base64,iVBORw0KGgo="


def norm(messages):
    p = OpenAICompatThinkProvider(host="https://api.openai.com", model="gpt-test")
    return p._normalize_messages_for_openai(messages, "sys")


def test_an_openai_image_part_reaches_the_wire():
    out = norm([{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": DATA}},
    ]}])
    assert out[1] == {"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": DATA}},
    ]}


def test_an_anthropic_image_block_is_converted_not_dropped():
    out = norm([{"role": "user", "content": [
        {"type": "text", "text": "and this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
        {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
    ]}])
    parts = out[1]["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert parts[2] == {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}


def test_an_image_alone_is_sent_without_an_empty_text_part():
    out = norm([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": DATA}}]}])
    assert out[1]["content"] == [{"type": "image_url", "image_url": {"url": DATA}}]


def test_text_only_part_lists_still_flatten_to_a_string():
    """The pre-existing shape is unchanged when there is no image."""
    out = norm([{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}])
    assert out[1] == {"role": "user", "content": "ab"}
