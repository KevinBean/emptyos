"""Adaptive reading routes for the browser extension.

The browser owns page observation and presentation. This module owns the
bounded language decision: which words merit an interruption, the user's
lightweight vocabulary profile, and a derived-response cache. Raw page text
is never persisted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from emptyos.capabilities import cloud_gate
from emptyos.sdk import (
    UNTRUSTED_SOURCE_CLAUSE,
    SpendCapReached,
    load_json,
    parse_llm_json,
    save_json,
    untrusted_block,
    web_route,
)

from . import word_bar
from .prompts import PROMPTS
from .shared import (
    DEFAULT_CEFR_BAND,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    reading_prompt,
)
from .vocab_schema import coerce_bool, coerce_difficulty

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401


# ─── Bind to DictionaryApp class as ──────────────────────────────────
#   api_reading_status        = _reading.api_reading_status
#   api_reading_models        = _reading.api_reading_models
#   api_reading_warm          = _reading.api_reading_warm
#   api_reading_settings      = _reading.api_reading_settings
#   api_reading_settings_save = _reading.api_reading_settings_save
#   api_reading_known         = _reading.api_reading_known
#   api_reading_analyze       = _reading.api_reading_analyze
#   api_reading_lookup        = _reading.api_reading_lookup
#   api_reading_save          = _reading.api_reading_save
#   api_reading_feedback      = _reading.api_reading_feedback
# Adding a new method here? Add a matching binding line in app.py — an unbound
# @web_route is a silent 404, not an error.
# ────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1
MAX_PAGE_CHARS = 8_000
MAX_CONTEXT_CHARS = 420
MAX_CACHE_ENTRIES = 500
MAX_ITEMS = 6
# Never highlight more than this share of the words on screen. Kevin's percentage —
# right as a CEILING, and wrong as a target: dense legal prose genuinely IS ~14% hard
# words while plain news is ~0%, so a fixed rate would over-flag one and starve the other.
SCREEN_WORD_SHARE = 0.10
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'-]{1,48}$")


def _source_label(url: object) -> str:
    """Host of the page the text came from — names the fenced block's origin."""
    try:
        return urlsplit(str(url or "")).hostname or "web page"
    except ValueError:
        return "web page"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_word(value: object) -> str:
    word = str(value or "").strip().strip(".,;:!?()[]{}\"\u201c\u201d\u2018\u2019")
    return word if _WORD_RE.match(word) else ""


def _clean_context(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_CONTEXT_CHARS]


def _clean_page_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_PAGE_CHARS]


def _cache_path(self) -> Path:
    return self.data_dir / "reading-cache.sqlite3"


# One connection per DB path, reused for the process lifetime. `with conn:` is a
# transaction scope, not a closing scope — connecting per call leaks the handle,
# and a Flow scan does a dozen cache writes. Mirrors sdk/think_cache.py.
_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def _cache_conn(self) -> sqlite3.Connection:
    path = _cache_path(self)
    key = str(path)
    if key not in _CONNECTIONS:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS entries (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                stored_at TEXT NOT NULL,
                last_hit TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0
            )"""
        )
        _CONNECTIONS[key] = conn
    return _CONNECTIONS[key]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _word_cache_key(word: str, context: str = "", native: str = "") -> str:
    # `native` is in the key: a gloss for a Spanish reader is not an answer for a
    # Chinese one, and serving the wrong language from cache would be silent.
    suffix = _digest(context.lower()) if context else "any"
    lang = (native or DEFAULT_NATIVE_LANGUAGE).strip().lower()[:16]
    return f"reading-word:v{SCHEMA_VERSION}:{lang}:{word.lower()}:{suffix}"


def _cache_get(self, key: str) -> dict | list | None:
    try:
        with _cache_conn(self) as conn:
            row = conn.execute(
                "SELECT payload FROM entries WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE entries SET hits = hits + 1, last_hit = ? WHERE cache_key = ?",
                (_now(), key),
            )
            return json.loads(row["payload"])
    except (OSError, sqlite3.Error, json.JSONDecodeError):
        return None


def _cache_put(self, key: str, payload: dict | list, provenance: dict) -> None:
    try:
        now = _now()
        with _cache_conn(self) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entries
                   (cache_key, payload, provider, model, stored_at, last_hit, hits)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    key,
                    json.dumps(payload, ensure_ascii=False),
                    str(provenance.get("provider") or ""),
                    str(provenance.get("model") or ""),
                    now,
                    now,
                ),
            )
            count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            overflow = count - MAX_CACHE_ENTRIES
            if overflow > 0:
                conn.execute(
                    "DELETE FROM entries WHERE cache_key IN "
                    "(SELECT cache_key FROM entries ORDER BY last_hit ASC LIMIT ?)",
                    (overflow,),
                )
    except (OSError, sqlite3.Error, TypeError):
        return


def _cache_stats(self) -> dict:
    try:
        with _cache_conn(self) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits FROM entries"
            ).fetchone()
            return {"entries": row["entries"], "hits": row["hits"], "limit": MAX_CACHE_ENTRIES}
    except (OSError, sqlite3.Error):
        return {"entries": 0, "hits": 0, "limit": MAX_CACHE_ENTRIES}


async def _cache_get_async(self, key: str) -> dict | list | None:
    return await asyncio.to_thread(_cache_get, self, key)


async def _cache_put_async(
    self, key: str, payload: dict | list, provenance: dict
) -> None:
    await asyncio.to_thread(_cache_put, self, key, payload, provenance)


async def _cache_stats_async(self) -> dict:
    return await asyncio.to_thread(_cache_stats, self)


# ── Settings: ONE source of truth, owned by the dictionary app ────────────
# The extension deliberately stores only host + token. Every reading preference
# (mode, display, languages, providers, per-site pauses) lives here, next to the
# profile and cache, so the browser is a client of the dictionary rather than a
# second settings silo that drifts from it.
SETTINGS_DEFAULTS: dict = {
    "mode": "off",  # off | ask | flow
    "display": "auto",  # auto | margin | corner | ink
    # Flow's model. NOT "paid" — Flow accepts a local model too (free, private, and
    # usually quicker than a cloud round trip), so the old name lied.
    "flow_provider": "",
    "local_provider": "",  # Ask's model — local only
    "excluded_hosts": [],
    # The reader's own CEFR band — the bar a word must clear to be worth interrupting
    # them for. "auto" infers it from their judgements (see `_band_for`). This MIRRORS
    # the canonical `dictionary.cefr_level` setting so the extension can read and write
    # it on the route it already uses; the value itself lives in the settings service,
    # next to every other app setting, and /settings and the panel cannot disagree.
    "cefr_level": "auto",
    # Mail, money, health and messaging hosts are read-free by default (the list
    # lives in the extension's reading-config.js, which is the only place that can
    # enforce it before the page is read). This is the reader's explicit opt-in for
    # a host on that list — empty is the safe state, and the one we ship.
    "allowed_private_hosts": [],
    "native_language": DEFAULT_NATIVE_LANGUAGE,
    "target_language": DEFAULT_TARGET_LANGUAGE,
    "pronounce": True,
    "rail": True,  # the scroll-following word list
    # Saving is rare and deliberate, so it is worth a strong model and a full
    # lexical entry. The card stays thin and fast; the NOTE gets polished.
    "enrich_on_save": True,
    "save_provider": "",  # "" = let the chain pick its strongest
}
_MODES = {"off", "ask", "flow"}
_DISPLAYS = {"auto", "margin", "corner", "ink"}
# "auto" means: infer it from what the reader has actually told us (see `_band_for`).
_CEFR_LEVELS = {"auto", "A1", "A2", "B1", "B2", "C1", "C2"}
# The canonical home of the level: an ordinary app setting, browsable at /settings,
# shared with the harvest bar. The reading-settings copy is a MIRROR of this, carried
# on the route the extension already uses — so the panel is a client of the setting
# rather than a second place to keep it.
CEFR_SETTING_KEY = "dictionary.cefr_level"

# What "auto" resolves to. The profile already derives a calibration from the words the
# reader has marked known vs hard (`_profile_summary`); this is the only thing in the
# system that has ever known anything about their level, and until they say otherwise
# it is the best guess we have.
_AUTO_BANDS = {
    "more-supportive": "B1-B2",
    "advanced-default": "B2-C1",
    "more-selective": "C1-C2",
}
# The same inference, as a single key the frequency bar can look up.
_AUTO_LEVELS = {
    "more-supportive": "B1",
    "advanced-default": "C1",
    "more-selective": "C2",
}


def _settings_path(self) -> Path:
    return self.data_dir / "reading-settings.json"


def _settings_normalise(raw: object) -> dict:
    """Coerce a settings dict. Pure — the read and the write share it, or they drift."""
    data = dict(raw) if isinstance(raw, dict) else {}
    # `paid_provider` was renamed when Flow stopped being cloud-only. Migrate on
    # read so a reader who already picked a Flow model does not silently lose it.
    legacy = data.pop("paid_provider", "")
    if legacy and not data.get("flow_provider"):
        data["flow_provider"] = legacy
    out = {**SETTINGS_DEFAULTS, **data}
    if out.get("mode") not in _MODES:
        out["mode"] = SETTINGS_DEFAULTS["mode"]
    if out.get("display") not in _DISPLAYS:
        out["display"] = SETTINGS_DEFAULTS["display"]
    level = str(out.get("cefr_level") or "").strip()
    level = level if level in _CEFR_LEVELS else (level.upper() if level.upper() in _CEFR_LEVELS else "")
    out["cefr_level"] = level or SETTINGS_DEFAULTS["cefr_level"]
    for key in ("excluded_hosts", "allowed_private_hosts"):
        hosts = out.get(key)
        out[key] = sorted(
            {str(h).strip().lower() for h in hosts if str(h).strip()}
        ) if isinstance(hosts, list) else []
    for flag in ("pronounce", "rail", "enrich_on_save"):
        out[flag] = coerce_bool(out.get(flag))
    for lang in ("native_language", "target_language"):
        out[lang] = str(out.get(lang) or SETTINGS_DEFAULTS[lang]).strip()[:32]
    return out


def _settings_load(self) -> dict:
    out = _settings_normalise(load_json(_settings_path(self), {}))
    # The level's home is the settings service — an ordinary app setting, browsable at
    # /settings, read by the harvest bar too. What lives in the reading blob is a
    # MIRROR, so the extension can read it on the route it already uses. The canonical
    # value wins on every read: if the two ever disagree, it is a silo.
    canonical = str(self.setting(CEFR_SETTING_KEY, "") or "").strip()
    if canonical in _CEFR_LEVELS:
        out["cefr_level"] = canonical
    return out


def _band_for(self, settings: dict | None = None, profile: dict | None = None) -> str:
    """The CEFR band a word must clear to be worth interrupting this reader for.

    An explicit level wins — the reader knows their own level, and the profile
    currently infers it from a handful of judgements. "auto" falls back to that
    inference, which is the only thing in the system that has ever known anything
    about their level.
    """
    settings = settings if isinstance(settings, dict) else _settings_load(self)
    level = str(settings.get("cefr_level") or "auto").strip()
    if level in _CEFR_LEVELS and level != "auto":
        return level
    profile = profile if isinstance(profile, dict) else _profile_load(self)
    calibration = _profile_summary(profile).get("calibration", "advanced-default")
    return _AUTO_BANDS.get(calibration, DEFAULT_CEFR_BAND)


def _level_for(self, settings: dict | None = None, profile: dict | None = None) -> str:
    """One CEFR key for the frequency bar — `_band_for` gives prose, this gives a key.

    "auto" is the reader's own judgements, read through the calibration the profile
    already derives. It is a guess; the picker is how they overrule it.
    """
    settings = settings if isinstance(settings, dict) else _settings_load(self)
    level = str(settings.get("cefr_level") or "auto").strip().upper()
    if level in word_bar.ZIPF_BAR:
        return level
    profile = profile if isinstance(profile, dict) else _profile_load(self)
    calibration = _profile_summary(profile).get("calibration", "advanced-default")
    return _AUTO_LEVELS.get(calibration, word_bar.DEFAULT_LEVEL)


def _cap_for(text: str) -> int:
    """The most words THIS screen may carry — an interruption budget, not a target.

    A fixed count cannot be right for both a 24-word chunk and a 500-word article, and
    when it was one, the model padded: a plain news page came back with `residents`,
    `pleased`, `review`, and a short chunk came back a fifth highlighted. So the count
    falls out of the bar, and this only stops the rail from swallowing a short screen.

    On a real screen (300+ words) this is always MAX_ITEMS, so it costs nothing where
    it is not needed. It binds only where the damage was.
    """
    words = len(str(text or "").split())
    if not words:
        return 0
    return max(1, min(MAX_ITEMS, math.ceil(words * SCREEN_WORD_SHARE)))


def _languages(self, body: dict | None = None) -> tuple[str, str]:
    """(native, target) — request override wins, else the stored setting."""
    settings = _settings_load(self)
    body = body if isinstance(body, dict) else {}
    native = str(body.get("native") or settings["native_language"]).strip()[:32]
    target = str(body.get("target") or settings["target_language"]).strip()[:32]
    return native or DEFAULT_NATIVE_LANGUAGE, target or DEFAULT_TARGET_LANGUAGE


def _profile_path(self) -> Path:
    return self.data_dir / "reading-profile.json"


def _profile_load(self) -> dict:
    data = load_json(_profile_path(self), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("known", {})
    data.setdefault("hard", {})
    data.setdefault("opened", {})
    data.setdefault("dismissed", {})
    data.setdefault("revision", 0)
    return data


def _profile_words(bucket: object, limit: int = 80) -> list[str]:
    if not isinstance(bucket, dict):
        return []
    ranked = sorted(
        bucket.items(),
        key=lambda pair: (int((pair[1] or {}).get("count", 0)), str((pair[1] or {}).get("last", ""))),
        reverse=True,
    )
    return [word for word, _ in ranked[:limit]]


def _profile_summary(profile: dict) -> dict:
    known_n = len(profile.get("known") or {})
    hard_n = len(profile.get("hard") or {})
    if known_n >= 20 and known_n > hard_n * 2:
        calibration = "more-selective"
    elif hard_n >= 8 and hard_n > known_n:
        calibration = "more-supportive"
    else:
        calibration = "advanced-default"
    return {
        "known_words": known_n,
        "hard_words": hard_n,
        "opened_words": len(profile.get("opened") or {}),
        "calibration": calibration,
        "revision": int(profile.get("revision") or 0),
    }


def _profile_signal(
    profile: dict, saved_words: list[str], lookup_frequency: dict
) -> str:
    """The reader's calibration as prompt text.

    No separate cache signature: this text is part of the analyze prompt, so
    think(cache=True) already keys on it — a profile change invalidates the
    cached analysis for free.
    """
    summary = _profile_summary(profile)
    saved = {_clean_word(word).lower() for word in saved_words[-80:]}
    known = set(_profile_words(profile.get("known")))
    repeated = {
        _clean_word(word).lower()
        for word, entry in (lookup_frequency or {}).items()
        if isinstance(entry, dict) and int(entry.get("count") or 0) >= 2
    }
    learning = (
        set(_profile_words(profile.get("hard")))
        | {word for word in saved if word}
        | {word for word in repeated if word}
    ) - known
    known_words = sorted(known)[:80]
    learning_words = sorted(learning)[:80]
    # `calibration` is deliberately NOT stated here any more. It said things like
    # "advanced-default", which is a second claim about the reader's level — and a
    # second claim wins over the first often enough to make the level setting a dead
    # control. The level is stated once, in the system prompt's bar, and this block
    # carries only what it alone knows: the words THIS reader has actually judged.
    prompt = (
        f"Words the reader explicitly marked known: {', '.join(known_words) or '(none)'}.\n"
        "Words still being learned (saved, repeatedly looked up, or marked difficult): "
        f"{', '.join(learning_words) or '(none)'}."
    )
    return prompt

# Providers that answer by spawning a subprocess. Correct, often free — and far too
# slow to sit in front of a reader: claude-cli costs ~33s on a full page, almost all
# of it process spawn. Reading happens while the reader waits, so these are not
# offered for reading at all. They are not "slower"; at this latency they are a
# different product.
_SLOW_TO_SPAWN = ("claude-cli", "codex", "gemini-cli")

# Shown when the daemon's monthly spend cap stops a paid reading call.
READING_LIMIT_MESSAGE = (
    "This month's AI limit is reached, so reading help can't run now. "
    "It resumes next month."
)

# How long a warmed local model is asked to stay resident. Long enough to read an
# article without paying the load again on the next screen.
_KEEP_ALIVE = "30m"


def _is_ollama(provider) -> bool:
    return getattr(provider, "name", "") == "ollama" or "11434" in str(
        getattr(provider, "host", "")
    )


def _provider_id(provider) -> str:
    """The provider's IDENTITY, which is not its name.

    A reader can add model variants (`/providers`), and a variant keeps the family
    name: two providers are both called "ollama" while serving different models. Key
    anything on the name and they collide — the panel offered one `ollama` and the
    other one's warmth, so a cold gemma4 variant reported itself over the qwen the
    reader actually reads with.
    """
    return str(getattr(provider, "variant_id", "") or getattr(provider, "name", ""))


async def _warmth(provider) -> dict:
    """Is this local model RESIDENT, or would the next call pay to load it?

    A cold local model is not merely slow — loading a 32k-context model onto a GPU
    that ComfyUI is already using can take longer than the browser will wait, and
    the reader experiences that as a reading layer that does not work. So warmth is
    a first-class fact about a provider, not an implementation detail.
    """
    if not _is_ollama(provider):
        return {"warmable": False, "warm": True}
    host = str(getattr(provider, "host", "")).rstrip("/")
    model = str(getattr(provider, "model", ""))
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{host}/api/ps", timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                if resp.status != 200:
                    return {"warmable": True, "warm": False}
                data = await resp.json()
    except Exception:
        # The server itself is unreachable — `available()` already reports that; do
        # not also claim it is merely cold, which would offer a Warm-up that cannot work.
        return {"warmable": True, "warm": False}
    loaded = {str(m.get("name") or m.get("model") or "") for m in data.get("models") or []}
    return {"warmable": True, "warm": model in loaded, "loaded": sorted(loaded)}


async def _warm_up(provider) -> dict:
    """Load the model and pin it. An empty prompt is enough to make ollama resident."""
    host = str(getattr(provider, "host", "")).rstrip("/")
    model = str(getattr(provider, "model", ""))
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{host}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": _KEEP_ALIVE},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    return {"ok": False, "error": f"ollama returned HTTP {resp.status}"}
                await resp.read()
    except Exception as exc:
        return {"ok": False, "error": f"could not reach the local model: {exc}"}
    return {"ok": True}


def _reading_providers(self, *, cloud: bool | None) -> list:
    """Every provider a READER could sensibly wait on.

    Excludes `human` (not an answer for a reading layer) and the subprocess-spawned
    CLIs (half a minute a screen). A reader is sitting there; a provider that cannot
    answer while they wait is not a slower option, it is a broken one.
    """
    out = []
    for provider in self.kernel.capability("think").providers_for(domain="text"):
        if provider.name == "human" or provider.name in _SLOW_TO_SPAWN:
            continue
        # A provider the monthly spend cap has stopped would be pinned below and
        # refused, shadowing a local model that could have answered.
        if cloud_gate.check(self.kernel, provider, "think", self.manifest.id):
            continue
        is_cloud = bool(getattr(provider, "is_cloud", False))
        is_local_model = getattr(provider, "auth_mode", "") == "local"
        if not (is_cloud or is_local_model):
            continue
        if cloud is True and not is_cloud:
            continue
        if cloud is False and is_cloud:
            continue
        out.append(provider)
    return out


async def _provider_for_tier(
    self, requested: str, *, cloud: bool | None
) -> tuple[str, str, str]:
    """Pick the provider for a tier. Returns (name, model, error).

    ``cloud=True`` cloud only · ``False`` local only · ``None`` either.

    A COLD local model is not eligible. Loading one can take longer than the reader
    (or the browser) will wait, and the reader experiences that as a layer that does
    not work — so warmth is checked here rather than discovered as a hang. Auto skips
    a cold model and moves on; an explicit choice says so, and the panel offers to
    warm it.
    """
    requested = str(requested or "").strip()
    candidates = _reading_providers(self, cloud=cloud)
    if requested:
        # By identity first; by family name only as a fallback, so a setting saved
        # before variants existed ("ollama") still resolves to the chain's first one.
        exact = [p for p in candidates if _provider_id(p) == requested]
        candidates = exact or [p for p in candidates if p.name == requested]
        if not candidates:
            return "", "", f"'{requested}' is not available for reading."

    for provider in candidates:
        try:
            if not await provider.available():
                continue
        except Exception:
            continue
        warmth = await _warmth(provider)
        if warmth.get("warmable") and not warmth.get("warm"):
            if requested:
                return "", "", (
                    f"The local model ({getattr(provider, 'model', provider.name)}) is not "
                    "loaded. Warm it up in the EmptyOS panel, or choose a cloud model."
                )
            continue  # Auto: a cold model is not worth the wait — move on
        return provider.name, str(getattr(provider, "model", "") or ""), ""

    return "", "", "No reading model is ready. Warm up the local model, or choose another in the EmptyOS panel."


def _reading_item(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    word = _clean_word(raw.get("word"))
    if not word:
        return None
    definition = _clean_context(raw.get("definition"))
    meaning = _clean_context(raw.get("meaning_in_context")) or definition
    if not meaning:
        return None
    # `native` is the language-neutral gloss. `chinese` is the legacy key — it is
    # still read (older cache rows, vault word notes) and still written (the vault
    # note schema has a `chinese:` field), so neither store needs a migration.
    native = _clean_context(raw.get("native") or raw.get("chinese"))[:80]
    return {
        "word": word,
        "part_of_speech": _clean_context(raw.get("part_of_speech"))[:48],
        # WHICH meaning of the word this is. A word has several, they are learned
        # separately, and without this a second encounter would overwrite the first.
        "sense_label": _clean_context(raw.get("sense_label"))[:40],
        "definition": definition or meaning,
        "meaning_in_context": meaning,
        "native": native,
        "chinese": native,
        "sentence": _clean_context(raw.get("sentence")),
        "source": str(raw.get("source") or "model"),
    }


def _rating_of(entry: dict) -> int:
    """The reader's 1-5 rating on a `_read_vault_word` entry, 0 when unrated.

    Two sites read it — the lookup card and the post-save read-back — and they
    must agree about what the note says, so the unwrapping lives once.
    """
    return coerce_difficulty((entry.get("meta") or {}).get("difficulty"))


async def _vault_item(self, word: str, context: str = "") -> dict | None:
    """Source #1 — the reader's OWN dictionary. Their note beats any model."""
    try:
        entry = await self._read_vault_word(word)
        if not entry:
            return None
        look = self._vault_as_lookup(entry)
    except Exception:
        return None
    if not look or not look.get("definition"):
        return None
    item = _reading_item({**look, "meaning_in_context": look.get("example") or "", "source": "vault"})
    if item:
        item["sentence"] = item["sentence"] or context
        # Only the vault source can carry a rating — a model answer describes a
        # word, this one describes the reader's history with it. `_reading_item`
        # builds a fixed shape and drops unknown keys, so it is set after.
        item["difficulty"] = _rating_of(entry)
    return item


async def _resolve_word(
    self, word: str, context: str, *, provider_hint: str, native: str, target: str
) -> tuple[dict | None, str]:
    """Resolve one word through the three sources, cheapest and most-owned first.

    1. the reader's saved vault note  — free, theirs, authoritative
    2. a cached high-quality answer   — free, already paid for
    3. the local model, on demand     — free, but a fresh call

    Returns (item, error). A miss at every tier is an error, never a silent empty.
    """
    item = await _vault_item(self, word, context)
    if item:
        return item, ""

    cached = await _cache_get_async(
        self, _word_cache_key(word, context, native)
    ) or await _cache_get_async(
        self, _word_cache_key(word, "", native)
    )
    if isinstance(cached, dict):
        return {**cached, "source": "cache"}, ""

    provider, model, why = await _provider_for_tier(self, provider_hint, cloud=False)
    if not provider:
        return None, why or "No local reading model is available."
    prompt = (
        "EmptyOS reading layer request.\n"
        f'Word: "{word}"\n'
        f"{untrusted_block(context or '(no sentence supplied)', label='page sentence')}"
    )
    try:
        raw = await self.think(
            prompt,
            system=reading_prompt(PROMPTS.reading_lookup_system, native, target),
            domain="text",
            strict_provider=provider,
            temperature=0.2,
        )
    except Exception as exc:
        return None, f"Local lookup unavailable: {exc}"
    item = _reading_item(parse_llm_json(raw, fallback={"word": word}))
    if not item:
        return None, "The local model did not return a usable definition."
    # The small local model sometimes omits `sentence`. We already know it — the
    # caller sent it — so backfill rather than lose the "where I met this word"
    # quote the card is built around.
    item["sentence"] = item["sentence"] or context
    item["source"] = "model"
    prov = self.last_provenance()
    if not prov.get("model"):
        prov["model"] = model or None
    await _cache_put_async(self, _word_cache_key(word, context, native), item, prov)
    return item, ""


def _parse_items(raw: object) -> list[dict]:
    data = raw if isinstance(raw, (dict, list)) else parse_llm_json(raw, fallback=[])
    if isinstance(data, dict):
        data = data.get("items", [])
    out: list[dict] = []
    seen: set[str] = set()
    for candidate in data if isinstance(data, list) else []:
        item = _reading_item(candidate)
        if not item or item["word"].lower() in seen:
            continue
        seen.add(item["word"].lower())
        out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return out


@web_route("GET", "/api/reading/status")
async def api_reading_status(self, request):
    profile = _profile_load(self)
    return {
        "ok": True,
        "profile": _profile_summary(profile),
        "cache": await _cache_stats_async(self),
        "policy": {
            "flow": "chosen model (cloud or local), cache first; cloud is consent-gated",
            "ask": "cached answer first, then the local model on demand",
            "raw_page_history_saved": False,
        },
    }


@web_route("GET", "/api/reading/models")
async def api_reading_models(self, request):
    """The models a reader can actually wait on, and whether each one is ready NOW.

    Warmth is reported, not hidden: a cold local model is offered with a way to load
    it rather than silently chosen and then experienced as a layer that hangs.
    """
    out = []
    for provider in _reading_providers(self, cloud=None):
        try:
            up = bool(await provider.available())
        except Exception:
            up = False
        warmth = await _warmth(provider) if up else {"warmable": _is_ollama(provider),
                                                     "warm": False}
        is_cloud = bool(getattr(provider, "is_cloud", False))
        out.append({
            "id": _provider_id(provider),      # identity — two providers may share a name
            "name": provider.name,
            "model": str(getattr(provider, "model", "") or ""),
            "kind": "cloud" if is_cloud else "local",
            "available": up,
            "warmable": bool(warmth.get("warmable")),
            "warm": bool(warmth.get("warm")),
            # Usable right now, without the reader waiting on a model load.
            "ready": up and (not warmth.get("warmable") or bool(warmth.get("warm"))),
        })
    return {"ok": True, "providers": out}


@web_route("POST", "/api/reading/warm")
async def api_reading_warm(self, request):
    """Load the local model and pin it. Slow on purpose — the reader asked for it."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    wanted = str((body or {}).get("provider") or "").strip()
    locals_ = [p for p in _reading_providers(self, cloud=False) if _is_ollama(p)]
    # By identity — warming "ollama" is ambiguous when two variants carry that name,
    # and loading the wrong one would leave the reader's own model still cold.
    target = next((p for p in locals_ if _provider_id(p) == wanted), None)
    if target is None and wanted:
        target = next((p for p in locals_ if p.name == wanted), None)
    if target is None and not wanted:
        target = locals_[0] if locals_ else None
    if target is None:
        return {"error": "No local model to warm up."}
    result = await _warm_up(target)
    if not result.get("ok"):
        return result
    warmth = await _warmth(target)
    return {"ok": True, "provider": _provider_id(target),
            "model": str(getattr(target, "model", "") or ""),
            "warm": bool(warmth.get("warm"))}


@web_route("GET", "/api/reading/settings")
async def api_reading_settings(self, request):
    """The extension holds only host + token; every preference is read from here."""
    return {"ok": True, "settings": _settings_load(self)}


@web_route("POST", "/api/reading/settings")
async def api_reading_settings_save(self, request):
    body = await request.json()
    if not isinstance(body, dict):
        return {"error": "expected a settings object"}
    current = _settings_load(self)
    # `paid_provider` is accepted so an extension that has not been reloaded yet
    # still lands its choice; `_settings_normalise` migrates it to `flow_provider`.
    known = set(SETTINGS_DEFAULTS) | {"paid_provider"}
    patch = {k: v for k, v in body.items() if k in known}
    if not patch:
        return {"error": "no known settings in the request"}
    settings = _settings_normalise({**current, **patch})
    save_json(_settings_path(self), settings)
    # The level is not ours to keep. Write it back to the setting that owns it, so the
    # panel and /settings are the same value seen from two places rather than two
    # values that will eventually disagree.
    if "cefr_level" in patch:
        svc = self.kernel.services.get_optional("settings")
        if svc is not None:
            svc.set(CEFR_SETTING_KEY, settings["cefr_level"])
    await self.emit("dictionary:reading_settings_changed", {"settings": settings})
    return {"ok": True, "settings": settings}


def _surface_words(text: str) -> set[str]:
    """Every distinct word on the screen, lowercased. Cheap and exact."""
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]{1,48}", text or "")}


async def _own_words_on_screen(self, text: str) -> list[dict]:
    """The reader's OWN study words, found on this screen without asking a model.

    Meeting a word you are actively learning, out in the wild, is the highest-value
    moment in the whole loop — and it used to be gated behind a model choosing to
    notice it. It never needed to be: the word is already in the vault, with a
    better gloss than the model would write, and the page text is right here. So
    this pass is deterministic, instant, free, and cannot be talked out of it by a
    model that decides the screen is easy.
    """
    on_screen = _surface_words(text)
    if not on_screen:
        return []

    profile = _profile_load(self)
    # Their own judgements first, then everything they have saved. NOT `known` —
    # a word they told us they know is the one word they do not need flagged.
    candidates: list[str] = []
    seen: set[str] = set()
    for source in (
        _profile_words(profile.get("hard")),
        [w for w, e in (self._load_freq() or {}).items()
         if isinstance(e, dict) and int(e.get("count") or 0) >= 2],
        await self._vault_words(),
    ):
        for raw in source:
            word = _clean_word(raw).lower()
            if word and word not in seen:
                seen.add(word)
                candidates.append(word)

    known = {w.lower() for w in _profile_words(profile.get("known"))}
    items: list[dict] = []
    for word in candidates:
        if word in known or word not in on_screen or len(items) >= MAX_ITEMS:
            continue
        item = await _vault_item(self, word, "")
        if item:
            items.append(item)
    return items


@web_route("POST", "/api/reading/known")
async def api_reading_known(self, request):
    """The reader's own words on this screen — no model, no cost, no waiting."""
    body = await request.json()
    text = _clean_page_text(body.get("text"))
    return {"ok": True, "items": await _own_words_on_screen(self, text)}


async def _analyze_by_frequency(
    self, *, text: str, body: dict, native: str, target: str,
    profile: dict, cap: int,
) -> dict:
    """Select by frequency; ask the model only what the words MEAN.

    The order is the whole design. The reader's own note answers first (free, and better
    than any model would write), then a remembered answer, and only what is left over
    costs a model call. A screen whose words are all known or all cached costs NOTHING —
    which the old whole-prompt cache could never manage, because it only hit when the
    entire screen was byte-identical.
    """
    level = _level_for(self, None, profile)
    known = {w.lower() for w in _profile_words(profile.get("known"))}
    words = word_bar.shortlist(text, level=level, known=known, cap=cap)
    meta = {"bar": "frequency", "level": level, "cap": cap}
    if not words:
        # Nothing on this screen is above this reader's bar. That is an ANSWER, and a
        # confident one — not the shrug a model gives when it cannot be bothered.
        return {"ok": True, "items": [], **meta}

    items: list[dict] = []
    misses: list[str] = []
    for word in words:
        own = await _vault_item(self, word, "")
        if own:
            items.append(own)
            continue
        cached = _cache_get(self, _word_cache_key(word, "", native))
        if isinstance(cached, dict):
            items.append({**cached, "source": "cache"})
            continue
        misses.append(word)

    if misses:
        # A model is needed only for words nobody has explained yet. A screen the reader's
        # own notes and the cache can already answer needs NO model — so a cold local
        # model, or none at all, must not stop it being read.
        provider, model, why = await _provider_for_tier(
            self, body.get("provider", ""), cloud=None
        )
        if not provider:
            return {"ok": True, "items": items, **meta, "gloss_error": why}
        prompt = (
            "EmptyOS reading layer request.\n"
            f"Explain these words as they are used in the passage: {', '.join(misses)}\n\n"
            f"{untrusted_block(text, label=_source_label(body.get('url')))}"
        )
        try:
            raw = await self.think(
                prompt,
                system=reading_prompt(PROMPTS.reading_gloss_system, native, target)
                + "\n\n"
                + UNTRUSTED_SOURCE_CLAUSE,
                domain="text",
                strict_provider=provider,
                temperature=0.2,
                cache=True,
            )
        except Exception as exc:
            # The words are still right — only their meanings are missing. Return what
            # the vault and the cache already knew rather than nothing.
            why = READING_LIMIT_MESSAGE if isinstance(exc, SpendCapReached) else str(exc)
            return {"ok": True, "items": items, **meta, "gloss_error": why}

        provenance = {"provider": provider, "model": model or None}
        wanted = set(misses)
        for item in _parse_items(raw):
            # The model may not add words. It was not asked which ones are hard.
            if item["word"].lower() not in wanted:
                continue
            _cache_put(self, _word_cache_key(item["word"], item["sentence"], native), item, provenance)
            _cache_put(self, _word_cache_key(item["word"], "", native), item, provenance)
            items.append(item)

    # Rarest first — if the rail is trimmed, it keeps the words that were hardest.
    items.sort(key=lambda it: word_bar.zipf(it["word"]))
    return {"ok": True, "items": items[:cap], **meta}


@web_route("POST", "/api/reading/analyze")
async def api_reading_analyze(self, request):
    body = await request.json()
    text = _clean_page_text(body.get("text"))
    if len(text) < 80:
        return {"ok": True, "items": []}

    native, target = _languages(self, body)
    profile = _profile_load(self)
    cap = _cap_for(text)

    # THE BAR. Frequency decides which words are hard — deterministically, monotonically,
    # and without an opinion. A model asked to do this ignored the reader's level
    # entirely (the local one) or flagged `council` for a C1 reader (the stronger one),
    # and when it did not fancy the page it returned nothing and the rail said "nothing
    # flagged". A frequency table cannot decline.
    #
    # Taken BEFORE a provider is resolved, and before the profile signal is assembled:
    # neither is needed to know which words are hard, and a screen the vault and cache
    # can already answer must not be held up by a model it does not need.
    if word_bar.available():
        return await _analyze_by_frequency(
            self, text=text, body=body, native=native, target=target,
            profile=profile, cap=cap,
        )

    # Fallback: no frequency table, so the model has to choose. Everything below exists
    # only for that path — the reader's word history as a prompt signal, and a CEFR band
    # stated in prose because there is no bar to state it as data.
    # Flow accepts a LOCAL model too: free, private, and usually quicker than a
    # cloud round trip. The reader chooses in the panel; auto prefers whatever the
    # chain puts first once subprocess-spawned providers are pushed to the back.
    provider, model, why = await _provider_for_tier(self, body.get("provider", ""), cloud=None)
    if not provider:
        return {"error": why or "No reading model is available."}
    signal = _profile_signal(profile, await self._vault_words(), self._load_freq())
    band = _band_for(self, None, profile)

    prompt = (
        "EmptyOS reading layer request.\n"
        f"{signal}\n\n"
        f"{untrusted_block(text, label=_source_label(body.get('url')))}"
    )
    try:
        # cache=True is the whole analysis cache: page text and the profile
        # signal are both in the prompt, and strict_provider keys the entry, so
        # a re-scan of an unchanged region never re-pays the paid call. The local
        # store below is only for the word→item cross-tier population that
        # think_cache cannot express.
        raw = await self.think(
            prompt,
            # Page text is the most attacker-controlled input in EmptyOS, so the
            # fence is unconditional here rather than behind source_fencer()'s
            # dark flag — there is no prior prompt shape to preserve.
            system=reading_prompt(
                PROMPTS.reading_analyze_system, native, target, level=band, cap=cap
            )
            + "\n\n"
            + UNTRUSTED_SOURCE_CLAUSE,
            domain="text",
            strict_provider=provider,
            temperature=0.2,
            cache=True,
        )
    except SpendCapReached:
        return {"error": READING_LIMIT_MESSAGE, "limit_reached": True}
    except Exception as exc:
        return {"error": f"Reading analysis unavailable: {exc}"}

    # Built from the provider we pinned, not last_provenance() — on a think-cache
    # hit no provider runs, so the recorded provenance would be a prior call's.
    provenance = {"provider": provider, "model": model or None}
    items = []
    for item in _parse_items(raw):
        # Source #1 still wins here: if the reader has already written their own
        # note for this word, the model's gloss must not overwrite it on the page.
        own = await _vault_item(self, item["word"], item["sentence"])
        if own:
            items.append(own)
            continue
        await _cache_put_async(
            self, _word_cache_key(item["word"], item["sentence"], native), item, provenance
        )
        await _cache_put_async(
            self, _word_cache_key(item["word"], "", native), item, provenance
        )
        items.append(item)
    return {"ok": True, "items": items, "provenance": provenance}


@web_route("POST", "/api/reading/lookup")
async def api_reading_lookup(self, request):
    body = await request.json()
    word = _clean_word(body.get("word"))
    context = _clean_context(body.get("context"))
    if not word:
        return {"error": "A single word is required."}
    native, target = _languages(self, body)
    item, error = await _resolve_word(
        self, word, context,
        provider_hint=str(body.get("provider") or ""), native=native, target=target,
    )
    if not item:
        return {"error": error}
    return {"ok": True, "item": item, "source": item.get("source", "model")}


async def _existing_senses(self, word: str) -> list[str]:
    """The sense labels a note already carries, so enrichment REUSES them.

    Without this, each enrichment invents its own labels ("academic-qualification"
    one day, "academic-level" the next), nothing matches, and re-saving a word
    silently doubles its sense list.
    """
    try:
        entry = await self._read_vault_word(word)
        if not entry:
            return []
        keys = (entry.get("meta") or {}).get("sense_keys")
        if isinstance(keys, list):
            return [str(k).strip() for k in keys if str(k).strip()]
        return [str(keys).strip()] if keys else []
    except Exception:
        return []


async def _enrich_word(self, word: str, item: dict, native: str, target: str) -> dict:
    """Turn the thin reading card into a real dictionary entry, with a STRONG model.

    The card is optimised for latency — a gloss and one sentence. A saved note is
    read months later, so it is worth IPA, inflections, every distinct sense with
    its own level and register, collocations and word family.

    Fails soft and returns {} — a model hiccup must never cost the reader the save
    they just asked for. The thin item is still a perfectly good note.
    """
    settings = _settings_load(self)
    provider = str(settings.get("save_provider") or "").strip()
    known = await _existing_senses(self, word)
    prompt = (
        f'Word: "{word}"\n'
        + (f"Senses already in the reader's note (reuse these labels when the "
           f"meaning matches): {', '.join(known)}\n" if known else "")
        + untrusted_block(
            item.get("sentence") or "(no sentence supplied)", label="page sentence"
        )
    )
    try:
        raw = await self.think(
            prompt,
            system=reading_prompt(PROMPTS.word_enrich_system, native, target)
            + "\n\n"
            + UNTRUSTED_SOURCE_CLAUSE,
            domain="text",
            # A full lexical entry is exactly the intricate, structured generation a
            # weak model garbles — ask the chain for its best (`.claude/rules/model-ability.md`).
            min_ability="strong",
            temperature=0.3,
            cache=True,
            **({"strict_provider": provider} if provider else {}),
        )
    except Exception:
        return {}
    data = parse_llm_json(raw, fallback=None)
    return data if isinstance(data, dict) else {}


def _senses_from_enrichment(data: dict, fallback: dict, native: str) -> list[dict]:
    """The met sense first (that is the one they are learning), then the rest."""
    raw = data.get("senses")
    out: list[dict] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        definition = _clean_context(entry.get("definition"))
        if not definition:
            continue
        out.append({
            "sense_label": _clean_context(entry.get("sense_label"))[:40],
            "definition": definition,
            "native": _clean_context(entry.get("native"))[:80],
            "level": str(entry.get("level") or "").strip()[:2].upper(),
            "register": str(entry.get("register") or "").strip().lower()[:16],
            "tags": entry.get("tags") or [],
            "example": _clean_context(entry.get("example")),
            "met_here": bool(entry.get("met_here")),
        })
        if len(out) >= 4:
            break
    if not out:
        # Enrichment failed or returned nothing usable — the card still knows a
        # meaning, and a thin note beats no note.
        return [{
            "sense_label": fallback.get("sense_label", ""),
            "definition": fallback.get("definition") or fallback.get("meaning_in_context", ""),
            "native": fallback.get("native") or fallback.get("chinese", ""),
            "level": "", "register": "", "tags": [],
            "example": fallback.get("meaning_in_context", ""),
            "met_here": True,
        }]
    out.sort(key=lambda s: not s["met_here"])
    return out


# What a verdict means for the note. "I know this" and "Still hard" are both
# judgements worth RECORDING — the reader wants the word in their vocabulary either
# way; what differs is the level it lands at, and whether it enters review.
#
# A verdict ROUTES a word; it never rates one. The 1-5 `difficulty` stars are the
# reader's own judgement and have exactly one writer — `set_word_difficulty`,
# reached from the star row (`vocab.py`: "theirs, not ours: nothing here ever rates
# a word on their behalf"). "Still hard" used to add a star, on the same card that
# shows the star row: two controls over one field, and the number is not cosmetic —
# it tie-breaks the review deck (`shared.py::due_card_key`) and the stats count
# only 4-5 stars as hard (`srs.py`), so repeated verdicts silently promoted a word
# the reader never rated.
_VERDICT = {
    "saved":  ("learning", True),
    "hard":   ("learning", True),   # actively studying → goes into SRS
    "known":  ("known", False),     # already theirs → recorded, but not drilled
}


async def _record_word(self, word: str, item: dict, action: str, body: dict) -> dict:
    """Enrich, then persist. The single write path for every reading-layer save."""
    status, enroll = _VERDICT[action]
    native, target = _languages(self, body)
    known = await _existing_senses(self, word)
    enriched = await _enrich_word(self, word, item, native, target) \
        if _settings_load(self).get("enrich_on_save", True) else {}
    senses = _senses_from_enrichment(enriched, item, native)
    primary = senses[0]
    # The word's OTHER meanings are catalogued ONCE, on the first save. On a
    # re-save only the sense actually met is folded in — otherwise every save
    # re-adds the whole catalogue under whatever labels the model invented that
    # day, and the note's sense list doubles each time.
    others = [] if known else senses[1:]

    result = await self.save_word(
        word=word,
        lemma=_clean_context(enriched.get("lemma")) or word,
        part_of_speech=_clean_context(enriched.get("part_of_speech"))
        or item.get("part_of_speech", ""),
        phonetic=_clean_context(enriched.get("ipa")),
        forms=enriched.get("forms") or [],
        definition=primary["definition"],
        meaning_in_context=item.get("meaning_in_context", ""),
        sense_label=primary["sense_label"],
        sense_tags=primary["tags"],
        level=primary["level"],
        register=primary["register"],
        example=primary["example"],
        native=native,
        gloss=primary["native"] or item.get("native") or item.get("chinese", ""),
        target=target,
        synonyms=enriched.get("synonyms") or [],
        antonyms=enriched.get("antonyms") or [],
        collocations=enriched.get("collocations") or [],
        word_family=enriched.get("word_family") or [],
        topics=enriched.get("topics") or [],
        etymology=_clean_context(enriched.get("etymology")),
        usage_notes=_clean_context(enriched.get("usage_notes")),
        source_url=str(body.get("source_url") or "")[:1000],
        sentence=item.get("sentence", ""),
        status=status,
        enroll_srs=enroll,
        extra_senses=others,
    )
    if result.get("ok"):
        # REPORT the rating the note already holds; never move it. Read rather than
        # trust `item`, which may carry a rating from before this save.
        #
        # An unreadable note leaves the key ABSENT, never 0. `_read_vault_word`
        # swallows the read into None but parses outside its guard, so a raise is
        # reachable — and 0 is not "we could not tell", it is "unrated". Reporting
        # it would blank the star row of a word rated 3 while the note kept the 3,
        # which is the exact harm this block exists to avoid: the rating reappears
        # on the next open, so the click reads as having eaten it. The card leaves
        # a row it was not told about alone.
        try:
            entry = await self._read_vault_word(word)
        except Exception:
            entry = None
        if entry:
            result["difficulty"] = _rating_of(entry)
    result["status"] = status
    result["enriched"] = bool(enriched)
    result["senses"] = len(senses)
    return result


@web_route("POST", "/api/reading/save")
async def api_reading_save(self, request):
    body = await request.json()
    item = body.get("item") if isinstance(body.get("item"), dict) else {}
    word = _clean_word(item.get("word") or body.get("word"))
    if not word:
        return {"error": "A word is required."}
    return await _record_word(self, word, item, "saved", body)


@web_route("POST", "/api/reading/feedback")
async def api_reading_feedback(self, request):
    body = await request.json()
    word = _clean_word(body.get("word")).lower()
    action = str(body.get("action") or "").strip().lower()
    if not word or action not in {"opened", "known", "hard", "dismissed", "saved"}:
        return {"error": "word and a valid action are required"}
    profile = _profile_load(self)
    bucket = "opened" if action == "saved" else action
    if bucket in {"known", "hard", "opened", "dismissed"}:
        entry = profile[bucket].setdefault(word, {"count": 0, "last": ""})
        entry["count"] = int(entry.get("count") or 0) + 1
        entry["last"] = _now()
    if action == "known":
        profile["hard"].pop(word, None)
    elif action == "hard":
        profile["known"].pop(word, None)
        profile["dismissed"].pop(word, None)
    elif action == "dismissed":
        profile["hard"].pop(word, None)
    if action in {"known", "hard", "dismissed"}:
        profile["revision"] = int(profile.get("revision") or 0) + 1
    save_json(_profile_path(self), profile)

    # A verdict IS a save. "I know this" and "Still hard" are both the reader
    # telling us where this word sits for them, and both belong in their vocabulary
    # — what differs is the level it lands at and whether it enters review. Without
    # this, a word judged in the rail was recorded only in a profile file the reader
    # can never read, and the vault knew nothing about it.
    recorded = {}
    item = body.get("item") if isinstance(body.get("item"), dict) else {}
    if action in _VERDICT and action != "saved" and item.get("word"):
        try:
            recorded = await _record_word(self, word, item, action, body)
        except Exception as exc:
            # The judgement is already in the profile; a failed write must not make
            # the click look broken.
            recorded = {"error": str(exc)}
    out = {
        "ok": True,
        "profile": _profile_summary(profile),
        "saved": bool(recorded.get("ok")),
        "status": recorded.get("status", ""),
    }
    # Absent stays absent. Defaulting to 0 here would undo the read-back's care
    # above and hand the card the "unrated" it specifically refused to claim.
    if "difficulty" in recorded:
        out["difficulty"] = recorded["difficulty"]
    return out
