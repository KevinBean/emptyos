"""Unit tests — voice-assistant shared.py STT caption-artifact blocklist.

Pure module, no daemon. The positives are the actual Whisper hallucinations
observed in data/apps/voice-assistant/chat_log.jsonl (2026-04 → 2026-06);
the negatives are real commands that must never be swallowed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SHARED = Path(__file__).parent.parent / "apps/public/standard/voice-assistant/shared.py"


@pytest.fixture(scope="module")
def shared():
    spec = importlib.util.spec_from_file_location("va_shared_under_test", _SHARED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ARTIFACTS = [
    "Thanks for watching!",
    "THANK YOU FOR WATCHING.",
    "Please like and subscribe",
    "📢 Share this video with your friends on social media.",
    "İzlediğiniz için teşekkür ederim.",   # Turkish İ casefold → combining dot
    "ご視聴ありがとうございました。",
    "視頻をご覧いただきありがとうございます。",
    "시청해주셔서 감사합니다.",
    "谢谢观看",
    "Merci d'avoir regardé",
    "Subtitles by the Amara.org community",
    "ZZZZZZZZZZZZZZ",
    "Zzzzzzzzzzzz",
]

REAL_INPUT = [
    "thanks for the help",
    "add a task to buy milk",
    "what are my tasks today",
    "我有泡菜、豆腐和芹菜，我可以做什么来吃",
    "🥰🥰🥰🥰🥰🥰🥰",          # repeated emoji is a genuine message, not noise
    "Hello, how are you?",
    "start a 25 minute focus session",
    "",
    "   ",
]


@pytest.mark.parametrize("text", ARTIFACTS)
def test_artifacts_detected(shared, text):
    assert shared.is_stt_artifact(text) is True


@pytest.mark.parametrize("text", REAL_INPUT)
def test_real_input_passes(shared, text):
    assert shared.is_stt_artifact(text) is False
