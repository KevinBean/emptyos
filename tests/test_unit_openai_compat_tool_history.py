from __future__ import annotations

from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider


def test_openai_normalizer_drops_orphan_tool_messages_and_keeps_valid_pairs():
    provider = OpenAICompatThinkProvider(
        host="https://api.openai.com",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        provider_name="openai",
    )

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "call_missing_parent", "content": "orphan"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "ok"},
    ]

    out = provider._normalize_messages_for_openai(messages, system="")

    assert out == [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "ok"},
    ]
