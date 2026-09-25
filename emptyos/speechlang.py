"""Speech-language detection for TTS voice AND provider selection.

Top-level and stdlib-only for the same reason as ``frontmatter.py``,
``fieldspec.py`` and ``composite_score.py``: its consumers must not pay for the
SDK package. ``emptyos/sdk/tts_cache.py`` states in its own docstring that it
imports "stdlib plus the app handle only ... so a caller can be tested without
booting one" — and importing ``emptyos.sdk.utils`` from there pulled **376**
modules including ``base_app``, breaking exactly that contract. The plugin and
``BaseApp.speak`` need the same rule, so it lives where all three can reach it
for free.

One rule, three consumers. If they disagree, ``speak()`` can steer a line to
edge-tts while edge-tts decides it is English and picks an English voice — the
same garbled output, one layer further on.
"""

from __future__ import annotations

# Hiragana is the discriminator, not "kana". Chinese never uses hiragana, while
# katakana DOES appear in Chinese text as a quoted foreign word — 他喜欢吃拉面
# ラーメン is 40% katakana and entirely Chinese, and counting it as Japanese
# routed that line away from the fix and straight back to the garbled provider.
# Hiragana also catches headline Japanese that a whole-kana ratio misses:
# 新型感染症対策本部会議を開催 carries a single を (7% of the string).
_HIRAGANA = ("ぁ", "ゟ")
_KATAKANA = ("゠", "ヿ")
_HAN = ("一", "鿿")

# A single particle in a 14-character headline is 7%, so the floor sits below
# that. Measured against real sentences: ordinary Japanese prose runs 44-61%
# hiragana, terse business Japanese 15%, and Chinese prose 0%.
HIRAGANA_FLOOR = 0.03
HAN_FLOOR = 0.1


def _share(text: str, lo: str, hi: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if lo <= c <= hi) / len(text)


def detect_speech_language(text: str) -> str:
    """Rough language tag for TTS voice/provider selection: "zh", "ja" or "en".

    Known limit, stated rather than papered over: text with NO hiragana is
    indistinguishable from Chinese by character range — 東京証券取引所株価指数
    reads as "zh". A caller holding Japanese text should pass ``language=``.
    """
    text = text or ""
    if not text:
        return "en"
    if _share(text, *_HIRAGANA) > HIRAGANA_FLOOR:
        return "ja"
    han = _share(text, *_HAN) + _share(text, *_KATAKANA)
    if han > HAN_FLOOR:
        return "zh"
    return "en"
