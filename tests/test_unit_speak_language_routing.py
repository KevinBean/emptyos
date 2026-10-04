"""Chinese TTS must not land on a provider that cannot pronounce it.

The `speak` chain is pinned kokoro-first on this machine, and kokoro's Mandarin
is unintelligible: a Whisper round-trip of
「你到底什么时候才肯告诉我真相？」 returned 「你到地神墨斯,野肉財坑告訴我…」
from kokoro and effectively word-perfect from edge-tts, same Whisper model. The
failure is silent — the waveform is fine, the mouth moves, and only a listener
or a round-trip can tell — so the routing that prevents it needs pinning.

Pure — no daemon, no kernel. Run:
    python -m pytest tests/test_unit_speak_language_routing.py -v
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.utils import (
    CJK_TTS_PROVIDER,
    detect_speech_language,
    speak_provider_preference,
)

REPO = Path(__file__).resolve().parent.parent


# --- language detection ----------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("你到底什么时候才肯告诉我真相？", "zh"),
    ("Give me one more hour", "en"),
    ("", "en"),
    ("こんにちは、元気ですか", "ja"),
    # A Chinese line carrying a Latin name is still a Chinese line — an English
    # voice garbles the whole of it, not just the Chinese part.
    ("你好 Kevin，今天的会议改到三点了", "zh"),
])
def test_language_detection(text, expect):
    assert detect_speech_language(text) == expect


def test_a_stray_cjk_char_in_english_does_not_flip_it():
    """The 10% floor: one borrowed glyph must not route an English line away."""
    assert detect_speech_language(
        "The character 道 appears once in this otherwise English sentence.") == "en"


# --- provider routing ------------------------------------------------------

def test_chinese_is_routed_to_edge_tts():
    assert speak_provider_preference("你到底什么时候才肯告诉我真相？") == [CJK_TTS_PROVIDER]


def test_english_is_left_alone():
    """None, not a list: English must keep whatever the machine pinned, which on
    this box is kokoro-first and deliberately so."""
    assert speak_provider_preference("Give me one more hour") is None


def test_a_pinned_chain_is_left_exactly_alone():
    """A pinned chain encodes constraints this function cannot see.

    voice-assistant pins kokoro to get WAV for thin clients with no MP3
    decoder (its own comment says so). edge-tts returns mp3, and the caller
    copies the result to a .wav filename — so reordering that chain turned a
    garbled-but-audible Chinese reply into a SILENT one. Format and locality
    live with the caller; correcting a chain from here is a guess.
    """
    assert speak_provider_preference(
        "你好世界，这是一个测试句子", prefer_provider=["kokoro"]) == ["kokoro"]


def test_a_kokoro_led_chain_still_gets_unintelligible_chinese():
    """The COST of the rule above, pinned so it stays visible.

    podcast defaults tts_providers to ["kokoro", "openai-tts"], so a Chinese
    episode still leads with the provider that garbles it. That is a config
    change on podcast, not something to paper over from here — but it must not
    be forgotten, so it is asserted rather than left as a comment.
    """
    assert speak_provider_preference(
        "你好世界，这是一个测试句子",
        prefer_provider=["kokoro", "openai-tts"],
    ) == ["kokoro", "openai-tts"]

def test_only_provider_is_the_absolute_escape_hatch():
    """Documented way to force exactly one provider, kokoro-for-Chinese
    included — otherwise nobody could ever test kokoro Mandarin again."""
    assert speak_provider_preference(
        "你好世界，这是一个测试句子", only_provider="kokoro") is None


def test_an_english_pinned_chain_is_never_touched():
    assert speak_provider_preference(
        "Give me one more hour", prefer_provider=["kokoro"]) == ["kokoro"]


def test_japanese_is_not_routed():
    """Deliberate: kokoro's Japanese has NOT been measured. Routing it on
    suspicion would be a guess wearing a fix's clothes."""
    assert speak_provider_preference("こんにちは、元気ですか、今日はいい天気") is None


# --- the wiring, not just the helper ---------------------------------------

def test_speak_passes_the_routed_provider_to_the_capability():
    """Drives the REAL BaseApp.speak against a fake kernel and asserts what
    reaches execute().

    The previous version of this test grepped an AST dump for two substrings
    and was vacuous: "prefer_provider" already appears in the dump as a keyword
    ARGUMENT, so deleting the two lines that are the whole fix left it green.
    That is audits.md Failure mode 3 exactly — the mutation a careless refactor
    makes has to be the one that goes red.
    """
    seen = {}

    class _Result:
        value = "/tmp/out.mp3"

    class _Cap:
        async def execute(self, **kw):
            seen.update(kw)
            return _Result()

    class _Kernel:
        def capability(self, _name):
            return _Cap()

    app = BaseApp.__new__(BaseApp)          # no kernel boot, no config
    app.kernel = _Kernel()
    app.manifest = type("M", (), {"id": "test-app"})()

    asyncio.run(app.speak("你到底什么时候才肯告诉我真相？"))
    assert seen.get("prefer_provider") == ["edge-tts"], (
        f"Chinese did not reach execute() routed: {seen!r}")

    seen.clear()
    asyncio.run(app.speak("Give me one more hour"))
    assert "prefer_provider" not in seen, (
        f"English must keep the machine's pinned chain: {seen!r}")

    seen.clear()
    asyncio.run(app.speak("你好世界，这是一个测试句子", prefer_provider=["kokoro"]))
    assert seen.get("prefer_provider") == ["kokoro"], (
        f"a pinned chain must survive untouched: {seen!r}")

def test_the_edge_tts_plugin_shares_the_same_rule():
    """Two copies of the rule can disagree: speak() could steer a line to
    edge-tts while edge-tts decides it is English and picks an English voice."""
    src = (REPO / "plugins" / "edge-tts" / "plugin.py").read_text(encoding="utf-8")
    assert "detect_speech_language" in src, (
        "the plugin re-grew its own detector — it must import the shared one")
    assert "def _detect_language(text" not in src, "private copy is back"


# --- the two defects the routing introduced, pinned so they cannot return ----

def test_edge_tts_is_classified_cloud_by_the_gate():
    """Asserts the RESOLVED property, not the source text.

    A grep for 'trust = "service"' passes on that string sitting in a comment.
    What matters is what `Provider.is_cloud` concludes, because that is what
    the consent gate and tts_cache._speak_local ("never send this text off the
    box") actually read. An undeclared, hostless provider resolves to LOCAL —
    not "unknown" — so the unsafe answer is the default.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "edge_tts_plugin_under_test", REPO / "plugins" / "edge-tts" / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert mod.EdgeTTSProvider().is_cloud is True, (
        "edge-tts resolves as LOCAL — Chinese TTS would skip the cloud-consent "
        "gate and satisfy local_only callers with an off-machine send")

def test_the_cache_key_tracks_the_routed_language():
    """A clip cached before the routing change must not keep being served.

    The key was a MODE ("local"/"auto"), not a provider, so a Chinese phrase
    synthesised by kokoro hashed identically afterwards — the fix would never
    reach anything already spoken, while new phrases came out fine. That is the
    worst signature a silent-failure bug can have.
    """
    src = (REPO / "emptyos" / "sdk" / "tts_cache.py").read_text(encoding="utf-8")
    assert "detect_speech_language" in src and "lang:" in src, (
        "cache key no longer varies with the routed language — stale garbled "
        "Chinese clips would be served forever")


@pytest.mark.parametrize("text", [
    "今日は会議の資料を準備する必要がある",   # ~15% kana, kanji-dense
    "彼女は新しい仕事を始めることを決めた",
])
def test_kanji_dense_japanese_is_not_mistaken_for_chinese(text):
    """Regression cover, not a fix: a review claimed the kana floor misreads
    kanji-dense Japanese as Chinese. Measured, it does not — these run 44% and
    61% kana, and even terse business Japanese is 15%. Kept because the rule now
    selects a PROVIDER, so a future tweak to it must not break these."""
    assert detect_speech_language(text) == "ja"
    assert speak_provider_preference(text) is None


# --- the katakana and headline cases the first rule got wrong ---------------

@pytest.mark.parametrize("text,expect", [
    # Chinese quoting a katakana loanword. The old rule counted ALL kana, so
    # this read as Japanese at 40% and was therefore NOT routed — silently back
    # to the garbled provider this whole change exists to avoid. Hiragana is the
    # discriminator: Chinese never uses it, katakana it sometimes quotes.
    ("他喜欢吃拉面ラーメン", "zh"),
    # Headline Japanese: one particle in 14 characters is 7% of the string, so a
    # whole-kana ratio at a 10% floor missed it.
    ("新型感染症対策本部会議を開催", "ja"),
    ("東京証券取引所株価指数先物取引", "zh"),   # zero hiragana — genuinely ambiguous
])
def test_the_hiragana_discriminator(text, expect):
    assert detect_speech_language(text) == expect


def test_the_detector_is_importable_without_the_sdk_package():
    """It lives at top level so a stdlib-only consumer can have it.

    `emptyos/sdk/__init__.py` imports base_app, so anything under
    `emptyos.sdk.*` drags in ~400 modules. tts_cache states in its own docstring
    that it stays light; the rule it needs must therefore not live in the SDK.
    """
    import subprocess
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys, emptyos.speechlang as m; "
         "assert m.detect_speech_language('你好世界，这是测试') == 'zh'; "
         "print('emptyos.sdk' in sys.modules)"],
        capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False", "importing the detector pulled in the SDK package"
