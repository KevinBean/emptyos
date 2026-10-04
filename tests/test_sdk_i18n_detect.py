"""Unit tests — emptyos.sdk.i18n.detect_lang_hint (script-based language hint).

Pure function, no daemon. Used to localize fixed system strings ("Want me to
apply that?") to the user's turn language; Latin input deliberately returns
None (could be en/es/fr — no honest guess).
"""

from __future__ import annotations

import pytest

from emptyos.sdk.i18n import detect_lang_hint


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我有泡菜豆腐", "zh"),
        ("好，帮我把泡菜豆腐汤加到菜谱", "zh"),
        ("ご視聴ありがとう", "ja"),
        ("漢字とかなが混ざった文", "ja"),          # kana present → ja even with CJK
        ("시청해주셔서 감사합니다", "ko"),
        ("Привет как дела", "ru"),
        ("สวัสดีครับ", "th"),
        ("hello there", None),
        ("what is the capital of France", None),
        ("add a task to call mom tomorrow", None),
        ("", None),
        ("12345 !!", None),
        ("ok 好", None),                            # 1 CJK char below the ≥2 floor
    ],
)
def test_detect_lang_hint(text, expected):
    assert detect_lang_hint(text) == expected
