"""voice-assistant — module-level constants + pure helpers shared across helper modules.

Extracted so helper modules (app spine, device.py, companions.py) can import
these directly without cycling through the spine ``.app`` module.

Pure functions only — no ``self``, no kernel access, no I/O.

The STT caption-artifact blocklist exists because Whisper, given silence or
room noise, confidently hallucinates YouTube-caption phrases ("Thanks for
watching!", its Korean/Japanese/Turkish/Chinese siblings, "subs by amara.org").
The chat log (2026-04 → 2026-06) shows these arriving as real turns and even
firing intents ("Logged."). Exact normalized match only — never fuzzy — so a
genuine command can't be swallowed.
"""

from __future__ import annotations

import re
import unicodedata

# Known Whisper hallucination family — normalized through _norm at import time
# (below) so both sides of the comparison get identical treatment (Turkish İ
# casefolds to i + combining dot; _norm strips the combining mark on both).
_RAW_ARTIFACT_PHRASES: tuple[str, ...] = (
    # English caption boilerplate
    "thanks for watching",
    "thank you for watching",
    "thanks for watching dont forget to subscribe",
    "please like and subscribe",
    "please subscribe",
    "like and subscribe",
    "dont forget to subscribe",
    "share this video with your friends on social media",
    "share this video with your friends on social media its a big help to me",
    "see you in the next video",
    "subtitles by the amaraorg community",
    # Japanese
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "視頻をご覧いただきありがとうございます",
    "最後までご視聴ありがとうございました",
    # Korean
    "시청해주셔서 감사합니다",
    "시청해 주셔서 감사합니다",
    "구독과 좋아요 부탁드립니다",
    # Turkish
    "izlediğiniz için teşekkür ederim",
    "izlediğiniz için teşekkürler",
    # Chinese
    "谢谢观看",
    "感谢观看",
    "谢谢大家观看",
    "请订阅",
    # French / Spanish / German common tails
    "merci davoir regardé",
    "gracias por ver el video",
    "danke fürs zuschauen",
)

# High-signal substrings — presence anywhere in the normalized utterance marks
# it as caption boilerplate (subtitle-credit lines vary too much for equality).
_ARTIFACT_SUBSTRINGS: tuple[str, ...] = (
    "amaraorg",
    "subtitles by",
    "字幕由",
    "字幕提供",
)

# A single letter repeated 6+ times ("ZZZZZZZZ") is mic noise transcribed as
# a letter run, never a command. Letters only — repeated emoji/CJK stay real
# input (a row of 🥰 is a genuine message, not an artifact).
_LETTER_RUN_RE = re.compile(r"^([A-Za-z])\1{5,}$")

# \w in Python's Unicode re already covers CJK ideographs, kana, and hangul.
_PUNCT_STRIP_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Casefold + strip punctuation/symbols/combining marks + collapse whitespace."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _PUNCT_STRIP_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip().casefold()
    # Drop combining marks so e.g. Turkish İ (casefold → i + U+0307) compares
    # equal to a plain i on both sides.
    return "".join(ch for ch in unicodedata.normalize("NFD", t) if not unicodedata.combining(ch))


_ARTIFACT_PHRASES: frozenset[str] = frozenset(_norm(p) for p in _RAW_ARTIFACT_PHRASES)


def is_stt_artifact(text: str) -> bool:
    """True when a transcription is a known Whisper caption-hallucination.

    Apply ONLY to STT-derived text (post-``listen``); typed input must never
    pass through this — a user genuinely typing "thanks for watching" to a
    companion is vanishingly unlikely, but the contract is cheaper to keep
    when the filter simply never sees typed text.
    """
    norm = _norm(text)
    if not norm:
        return False
    if norm in _ARTIFACT_PHRASES:
        return True
    if any(s in norm for s in _ARTIFACT_SUBSTRINGS):
        return True
    compact = norm.replace(" ", "")
    if _LETTER_RUN_RE.match(compact):
        return True
    return False
