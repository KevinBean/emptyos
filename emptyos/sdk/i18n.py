"""i18n — language registry + translation cache for the `translate` capability.

English is the single *authored* language in EmptyOS; every other language is
**derived** from English and cached here, so the same string is never
translated twice. That cache is the determinism guarantee — it's what turns a
non-deterministic LLM translation into a stable one (and saves the cost of
re-translating). The cache lives at ``data/i18n/<lang>.json`` as a flat
``{english: translated}`` dict — gettext-shaped, but **machine-authored**: no
human ever writes or maintains it.

To support a new language end-to-end, add one row to ``LANGUAGES`` (a human
name for the LLM prompt + the NLLB FLORES-200 code for the local MT provider).
Nothing else changes — apps stay English-only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Iterable

# Per-language locks serialize the load→translate→save read-modify-write so two
# concurrent batches for the same language can't lose each other's new entries
# (last-writer-wins drop). Keyed by lang; created lazily. Same shape as the
# journal `_daily_lock()` pattern (CLAUDE.md § vault read-modify-write races).
_cache_locks: dict[str, asyncio.Lock] = {}


def _lock_for(lang: str) -> asyncio.Lock:
    lk = _cache_locks.get(lang)
    if lk is None:
        lk = _cache_locks[lang] = asyncio.Lock()
    return lk

# code -> {name (for the LLM prompt), nllb (FLORES-200 code for NLLB-200),
# native (endonym, shown in the language picker)}. Add a row per language.
LANGUAGES: dict[str, dict] = {
    "en": {"name": "English", "nllb": "eng_Latn", "native": "English"},
    "zh": {"name": "Simplified Chinese", "nllb": "zho_Hans", "native": "中文"},
    "zh-Hant": {"name": "Traditional Chinese", "nllb": "zho_Hant", "native": "繁體中文"},
    "ja": {"name": "Japanese", "nllb": "jpn_Jpan", "native": "日本語"},
    "ko": {"name": "Korean", "nllb": "kor_Hang", "native": "한국어"},
    "es": {"name": "Spanish", "nllb": "spa_Latn", "native": "Español"},
    "fr": {"name": "French", "nllb": "fra_Latn", "native": "Français"},
    "de": {"name": "German", "nllb": "deu_Latn", "native": "Deutsch"},
    "pt": {"name": "Portuguese", "nllb": "por_Latn", "native": "Português"},
    "ru": {"name": "Russian", "nllb": "rus_Cyrl", "native": "Русский"},
    "ar": {"name": "Arabic", "nllb": "arb_Arab", "native": "العربية"},
    "hi": {"name": "Hindi", "nllb": "hin_Deva", "native": "हिन्दी"},
    "id": {"name": "Indonesian", "nllb": "ind_Latn", "native": "Bahasa Indonesia"},
    "vi": {"name": "Vietnamese", "nllb": "vie_Latn", "native": "Tiếng Việt"},
    "it": {"name": "Italian", "nllb": "ita_Latn", "native": "Italiano"},
}

# Right-to-left scripts — the chrome layer flips <html dir> for these.
RTL = {"ar", "he", "fa", "ur"}


def lang_name(code: str) -> str:
    """Human-readable language name for the LLM translate prompt."""
    return (LANGUAGES.get(code) or {}).get("name") or code


def nllb_code(code: str) -> str:
    """FLORES-200 code for the NLLB-200 provider, or '' if unsupported."""
    return (LANGUAGES.get(code) or {}).get("nllb") or ""


def is_rtl(code: str) -> bool:
    return code in RTL


def detect_lang_hint(text: str) -> str | None:
    """Best-effort script-based language hint for a user utterance.

    Pure Unicode-range counting — no deps, no LLM, no network. Returns a
    ``LANGUAGES`` code when the text is dominated by a non-Latin script,
    else ``None`` (Latin text could be en/es/fr/…, so no guess is honest).
    Intended for localizing short fixed system strings ("Want me to apply
    that?") to match the user's turn — NOT a general language detector.
    """
    if not text:
        return None
    counts = {"hangul": 0, "kana": 0, "cjk": 0, "cyrillic": 0, "thai": 0, "arabic": 0}
    letters = 0
    for ch in text:
        cp = ord(ch)
        if not ch.isalpha():
            continue
        letters += 1
        if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            counts["hangul"] += 1
        elif 0x3040 <= cp <= 0x30FF:
            counts["kana"] += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            counts["cjk"] += 1
        elif 0x0400 <= cp <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0E00 <= cp <= 0x0E7F:
            counts["thai"] += 1
        elif 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F:
            counts["arabic"] += 1
    if letters == 0:
        return None
    script, n = max(counts.items(), key=lambda kv: kv[1])
    # Need a real presence: ≥2 script chars AND ≥30% of all letters.
    if n < 2 or n / letters < 0.3:
        return None
    if script == "kana":
        return "ja"
    if script == "cjk":
        # Kana anywhere → Japanese even if ideographs dominate.
        return "ja" if counts["kana"] > 0 else "zh"
    return {"hangul": "ko", "cyrillic": "ru", "thai": "th", "arabic": "ar"}[script]


def _cache_path(data_dir, lang: str) -> Path:
    safe = lang.replace("/", "_").replace("\\", "_")
    return Path(data_dir) / "i18n" / f"{safe}.json"


def load_cache(data_dir, lang: str) -> dict:
    p = _cache_path(data_dir, lang)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(data_dir, lang: str, cache: dict) -> None:
    p = _cache_path(data_dir, lang)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(p)


async def translate_cached(kernel, lang: str, strings: Iterable[str]) -> dict:
    """Return ``{english: translated}`` for ``strings``, using + filling the cache.

    English passthrough and already-cached strings cost nothing; only cache
    misses hit the ``translate`` capability (local NLLB plugin → LLM fallback).
    New translations are persisted, so the same string is never translated
    twice. On any failure the original English is returned unchanged — this
    function never raises, so a missing provider degrades to English rather
    than breaking the page.
    """
    uniq: list[str] = []
    seen: set[str] = set()
    for s in strings:
        if isinstance(s, str) and s.strip() and s not in seen:
            seen.add(s)
            uniq.append(s)
    if lang == "en" or not uniq:
        return {s: s for s in uniq}

    data_dir = kernel.config.data_dir
    # Lock the whole load→translate→save so concurrent batches for this language
    # neither lose each other's writes nor double-call the provider for the same
    # misses. The provider await runs under the lock (it never re-enters here).
    async with _lock_for(lang):
        cache = load_cache(data_dir, lang)
        misses = [s for s in uniq if s not in cache]
        if misses:
            translated = None
            try:
                result = await kernel.capability("translate").execute(
                    texts=misses, to=lang, source="en"
                )
                translated = getattr(result, "value", None)
            except Exception:
                translated = None
            if isinstance(translated, list) and len(translated) == len(misses):
                for src, dst in zip(misses, translated):
                    if isinstance(dst, str) and dst:
                        cache[src] = dst
                try:
                    save_cache(data_dir, lang, cache)
                except Exception:
                    pass
            # On failure the misses stay uncached and fall through to English.

        return {s: cache.get(s, s) for s in uniq}
